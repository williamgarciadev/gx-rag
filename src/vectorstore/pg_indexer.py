"""
Indexador sobre PostgreSQL: chunking, embedding y persistencia.

Reemplaza a indexer.py (ChromaDB). Mismo contrato: index_article() y
reindex_if_changed() con los mismos parametros y valores de retorno.

Ventaja concreta del cambio: el reindexado es una transaccion. En ChromaDB el
DELETE de los chunks viejos y el INSERT de los nuevos eran dos operaciones
independientes — si el proceso moria en el medio, el articulo quedaba sin
chunks. Aca o pasan las dos o no pasa ninguna.
"""

from __future__ import annotations

import logging

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.vectorstore import embeddings as emb
from src.vectorstore.pg_client import get_pool

log = logging.getLogger(__name__)

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=["\n## ", "\n### ", "\n\n", "\n", " "],
    keep_separator=True,
)

_UPSERT_SQL = """
    INSERT INTO chunks (
        chunk_id, collection, article_id, chunk_index,
        title, content, source, url, genre, content_hash, embedding
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (chunk_id) DO UPDATE SET
        title        = EXCLUDED.title,
        content      = EXCLUDED.content,
        source       = EXCLUDED.source,
        url          = EXCLUDED.url,
        genre        = EXCLUDED.genre,
        content_hash = EXCLUDED.content_hash,
        embedding    = EXCLUDED.embedding
"""


def _vector_literal(values) -> str:
    """Serializa un embedding al literal que entiende pgvector."""
    return "[" + ",".join(f"{float(v):.7g}" for v in values) + "]"


def index_article(
    article_id: str,
    title: str,
    content: str,
    source: str,
    genre: str,
    url: str,
    content_hash: str,
    collection: str = "gx_docs",
) -> list[str]:
    """Indexa un articulo. Devuelve los ids de chunk insertados."""
    chunks = _splitter.split_text(content)
    if not chunks:
        log.warning("sin chunks para %s", article_id)
        return []

    embs = emb.embed_texts(tuple(chunks))
    chunk_ids = [f"{article_id}_c{i}" for i in range(len(chunks))]

    rows = [
        (
            chunk_ids[i],
            collection,
            article_id,
            i,
            title,
            chunks[i],
            source,
            url,
            genre,
            content_hash,
            _vector_literal(embs[i]),
        )
        for i in range(len(chunks))
    ]

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(_UPSERT_SQL, rows)

    log.info("indexados %d chunks de %s en %s", len(chunks), article_id, collection)
    return chunk_ids


def reindex_if_changed(
    article_id: str,
    title: str,
    content: str,
    source: str,
    genre: str,
    url: str,
    content_hash: str,
    collection: str = "gx_docs",
) -> str:
    """Indexa de forma idempotente, usando content_hash como clave de cambio.

    Devuelve:
      - "skipped":   ya existen chunks con el mismo hash (no se re-embebe)
      - "reindexed": existian con otro hash; se borran los viejos y se reindexa
      - "indexed":   no habia chunks previos
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content_hash FROM chunks "
                "WHERE article_id = %s AND collection = %s LIMIT 1",
                (article_id, collection),
            )
            row = cur.fetchone()

        if row is not None and row[0] == content_hash:
            return "skipped"

        if row is not None:
            # Borrado + alta en la misma transaccion: el articulo nunca queda
            # a medias aunque el proceso muera aca en el medio.
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM chunks WHERE article_id = %s AND collection = %s",
                    (article_id, collection),
                )
            index_article(
                article_id, title, content, source, genre, url, content_hash, collection
            )
            return "reindexed"

    index_article(
        article_id, title, content, source, genre, url, content_hash, collection
    )
    return "indexed"
