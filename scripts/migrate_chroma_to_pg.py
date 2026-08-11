"""
Migra el corpus de ChromaDB a PostgreSQL + pgvector.

No recalcula embeddings: los lee tal cual estan en Chroma y los inserta. Migrar
50k vectores es transferencia de datos, no inferencia — no hace falta la GPU ni
volver a descargar el modelo.

    python scripts/migrate_chroma_to_pg.py --dsn "postgresql://gxrag:...@localhost:5433/gxrag"

Idempotente: usa INSERT ... ON CONFLICT DO UPDATE, asi que se puede repetir.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Iterator

import chromadb
import psycopg

log = logging.getLogger("migrate")

# Chroma pagina internamente contra SQLite y desborda su limite de variables si
# se le pide una coleccion entera de una vez. Mismo tamaño que usa retriever.py.
PAGE_SIZE = 5_000

# Filas por lote de COPY. Cada fila lleva 1024 floats, asi que lotes muy grandes
# solo aumentan el pico de memoria sin mejorar el tiempo.
COPY_BATCH = 1_000

INSERT_SQL = """
    INSERT INTO chunks (
        chunk_id, collection, article_id, chunk_index,
        title, content, source, url, genre, content_hash, embedding
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (chunk_id) DO UPDATE SET
        collection   = EXCLUDED.collection,
        article_id   = EXCLUDED.article_id,
        chunk_index  = EXCLUDED.chunk_index,
        title        = EXCLUDED.title,
        content      = EXCLUDED.content,
        source       = EXCLUDED.source,
        url          = EXCLUDED.url,
        genre        = EXCLUDED.genre,
        content_hash = EXCLUDED.content_hash,
        embedding    = EXCLUDED.embedding
"""


def iter_chroma_pages(collection) -> Iterator[tuple[list, list, list, list]]:
    """Recorre una coleccion de Chroma en paginas de PAGE_SIZE."""
    offset = 0
    while True:
        page = collection.get(
            include=["embeddings", "documents", "metadatas"],
            limit=PAGE_SIZE,
            offset=offset,
        )
        ids = page.get("ids") or []
        if not ids:
            return
        yield (
            ids,
            page.get("documents") or [],
            page.get("metadatas") or [],
            page.get("embeddings") if page.get("embeddings") is not None else [],
        )
        offset += PAGE_SIZE


def to_row(chunk_id: str, doc: str, meta: dict, emb) -> tuple:
    """Arma una fila de `chunks` a partir de un chunk de Chroma."""
    meta = meta or {}
    # pgvector acepta el literal de texto '[x,y,z]' para el tipo vector.
    embedding = "[" + ",".join(f"{float(v):.7g}" for v in emb) + "]"
    return (
        chunk_id,
        meta.get("_collection", ""),
        # "id" en la metadata de Chroma es el id del articulo, no el del chunk.
        str(meta.get("id", chunk_id)),
        int(meta.get("chunk_index", 0)),
        str(meta.get("title", "")),
        doc or "",
        str(meta.get("source", "")),
        str(meta.get("url", "")),
        str(meta.get("genre", "")),
        str(meta.get("content_hash", "")),
        embedding,
    )


def migrate_collection(chroma_client, conn, name: str) -> int:
    """Migra una coleccion completa. Devuelve la cantidad de filas insertadas."""
    col = chroma_client.get_collection(name)
    total = col.count()
    if total == 0:
        log.info("%s: vacia, se omite", name)
        return 0

    log.info("%s: %d chunks a migrar", name, total)
    done = 0
    started = time.time()

    with conn.cursor() as cur:
        for ids, docs, metas, embs in iter_chroma_pages(col):
            if len(embs) != len(ids):
                raise RuntimeError(
                    f"{name}: Chroma devolvio {len(embs)} embeddings para {len(ids)} ids"
                )

            batch = []
            for i, chunk_id in enumerate(ids):
                meta = dict(metas[i] or {})
                meta["_collection"] = name
                batch.append(to_row(chunk_id, docs[i], meta, embs[i]))

                if len(batch) >= COPY_BATCH:
                    cur.executemany(INSERT_SQL, batch)
                    done += len(batch)
                    batch.clear()
                    log.info("  %s: %d/%d", name, done, total)

            if batch:
                cur.executemany(INSERT_SQL, batch)
                done += len(batch)
                log.info("  %s: %d/%d", name, done, total)

    conn.commit()
    log.info("%s: %d filas en %.1fs", name, done, time.time() - started)
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True, help="DSN de PostgreSQL")
    parser.add_argument("--chroma", default="data/chroma", help="Path del store Chroma")
    parser.add_argument(
        "--collections",
        default="",
        help="Colecciones separadas por coma (por defecto: todas las que existan)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    chroma_client = chromadb.PersistentClient(path=args.chroma)
    available = [c.name for c in chroma_client.list_collections()]
    wanted = [c.strip() for c in args.collections.split(",") if c.strip()] or available

    faltantes = [c for c in wanted if c not in available]
    if faltantes:
        log.error("colecciones inexistentes en Chroma: %s", ", ".join(faltantes))
        return 1

    total = 0
    with psycopg.connect(args.dsn) as conn:
        # El indice HNSW encarece cada INSERT. Sale mas barato construirlo una
        # sola vez sobre la tabla ya cargada.
        with conn.cursor() as cur:
            log.info("drop temporal del indice HNSW")
            cur.execute("DROP INDEX IF EXISTS chunks_embedding_hnsw")
        conn.commit()

        try:
            for name in wanted:
                total += migrate_collection(chroma_client, conn, name)
        finally:
            log.info("reconstruyendo indice HNSW (puede tardar varios minutos)")
            started = time.time()
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw "
                    "ON chunks USING hnsw (embedding vector_cosine_ops)"
                )
                cur.execute("ANALYZE chunks")
            conn.commit()
            log.info("indice HNSW listo en %.1fs", time.time() - started)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT collection, count(*) FROM chunks GROUP BY collection "
                "ORDER BY collection"
            )
            log.info("--- estado final en PostgreSQL ---")
            for coleccion, cantidad in cur.fetchall():
                log.info("  %-12s %d", coleccion, cantidad)

    log.info("migradas %d filas", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
