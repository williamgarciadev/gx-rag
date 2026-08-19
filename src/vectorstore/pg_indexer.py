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

import json
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
        title, content, source, url, genre, content_hash, embedding, meta
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (chunk_id) DO UPDATE SET
        collection   = EXCLUDED.collection,
        title        = EXCLUDED.title,
        content      = EXCLUDED.content,
        source       = EXCLUDED.source,
        url          = EXCLUDED.url,
        genre        = EXCLUDED.genre,
        content_hash = EXCLUDED.content_hash,
        embedding    = EXCLUDED.embedding,
        meta         = EXCLUDED.meta
"""
# `collection` MUST be in the DO UPDATE list. chunk_id is derived from
# article_id + index, so a conflict means "same article again" — and re-indexing
# an article INTO ANOTHER COLLECTION has to move it. Leaving it out made the
# caller's requested collection silently unreachable: add_from_url reported
# writing to gx_docs while the rows stayed in gx_syntax (measured 2026-08-11 on
# the WWP article 1149). Same failure shape as the handler bug it hid.
#
# `meta` va en el DO UPDATE por el MISMO motivo exacto: un conflicto de chunk_id
# significa "el mismo objeto otra vez", y reindexar con metadatos corregidos tiene
# que actualizarlos. Dejarlo afuera haria que corregir el obj_type o el module de
# un objeto ya indexado no tuviera efecto, y el filtro seguiria devolviendo lo
# viejo sin que nada falle.


def _vector_literal(values) -> str:
    """Serializa un embedding al literal que entiende pgvector."""
    return "[" + ",".join(f"{float(v):.7g}" for v in values) + "]"


def _write_chunks(
    conn,
    article_id: str,
    title: str,
    content: str,
    source: str,
    genre: str,
    url: str,
    content_hash: str,
    collection: str,
    *,
    meta: dict | None = None,
    chunks: list[tuple[str, str]] | None = None,
) -> list[str]:
    """Chunkea, embebe y escribe usando la conexion RECIBIDA.

    Recibe la conexion en vez de pedirsela al pool porque reindex_if_changed
    necesita que el DELETE de los chunks viejos y este INSERT vivan en la misma
    transaccion. Con una conexion propia el INSERT se quedaba esperando el lock
    que tomo el DELETE de la otra: deadlock, y el pool agotandose de a una
    conexion por reindexado.

    meta / chunks son KEYWORD-ONLY a proposito. Las 9 posiciones de arriba las
    consumen llamadores POSICIONALES —bulk_loader.py pasa doc.url DOS VECES, en
    `source` y en `url`— y hay tests que congelan ese orden. Una posicion 10 y 11
    en esa cadena es la forma mas barata de que `meta` termine escrito en `genre`
    sin que nada falle ni ningun test lo note.

    chunks: [(chunk_kind, texto)] ya partido por el llamador, para documentos cuya
    estructura la conoce el llamador y no el splitter (una ficha de objeto GeneXus
    se parte por seccion, no por 800 chars). Si es None se usa el splitter de
    siempre, asi que gx_docs / bt_docs no cambian en nada.
    """
    if chunks is not None:
        kinds = [kind for kind, _ in chunks]
        textos = [texto for _, texto in chunks]
    else:
        textos = _splitter.split_text(content)
        kinds = [None] * len(textos)

    if not textos:
        log.warning("sin chunks para %s", article_id)
        return []

    embs = emb.embed_texts(tuple(textos))
    chunk_ids = [f"{article_id}_c{i}" for i in range(len(textos))]

    def _meta_de(i: int) -> str:
        """meta del chunk i, con su chunk_kind si el llamador partio por seccion.

        El chunk_kind viaja en TODOS los sub-chunks de una seccion: si una seccion
        larga se parte en varios, el filtro por chunk_kind tiene que seguir
        encontrandolos a todos.
        """
        if meta is None and kinds[i] is None:
            return "{}"
        completa = dict(meta or {})
        if kinds[i] is not None:
            completa["chunk_kind"] = kinds[i]
        return json.dumps(completa, ensure_ascii=False)

    rows = [
        (
            chunk_ids[i],
            collection,
            article_id,
            i,
            title,
            textos[i],
            source,
            url,
            genre,
            content_hash,
            _vector_literal(embs[i]),
            _meta_de(i),
        )
        for i in range(len(textos))
    ]

    with conn.cursor() as cur:
        cur.executemany(_UPSERT_SQL, rows)

    log.info("indexados %d chunks de %s en %s", len(textos), article_id, collection)
    return chunk_ids


def index_article(
    article_id: str,
    title: str,
    content: str,
    source: str,
    genre: str,
    url: str,
    content_hash: str,
    collection: str = "gx_docs",
    *,
    meta: dict | None = None,
    chunks: list[tuple[str, str]] | None = None,
) -> list[str]:
    """Indexa un articulo. Devuelve los ids de chunk insertados.

    OJO: no borra chunks huerfanos. Si el documento se acorto, los `_cN` viejos
    sobreviven por encima del ON CONFLICT y siguen apareciendo en busquedas. Para
    ingesta idempotente usar reindex_if_changed.
    """
    with get_pool().connection() as conn:
        return _write_chunks(
            conn, article_id, title, content, source, genre, url,
            content_hash, collection, meta=meta, chunks=chunks,
        )


def reindex_if_changed(
    article_id: str,
    title: str,
    content: str,
    source: str,
    genre: str,
    url: str,
    content_hash: str,
    collection: str = "gx_docs",
    *,
    meta: dict | None = None,
    chunks: list[tuple[str, str]] | None = None,
) -> str:
    """Indexa de forma idempotente, usando content_hash como clave de cambio.

    Devuelve:
      - "skipped":   ya existen chunks con el mismo hash (no se re-embebe)
      - "reindexed": existian con otro hash; se borran los viejos y se reindexa
      - "indexed":   no habia chunks previos

    INVARIANTE de la que depende todo esto: el SELECT de abajo lee el hash con
    LIMIT 1 y SIN ORDER BY, o sea que trae UN CHUNK CUALQUIERA del articulo. Es
    correcto solo porque _write_chunks escribe el MISMO content_hash en todas las
    filas del articulo. Si alguna vez se hasheara por chunk, la decision
    skipped/reindexed pasaria a ser no deterministica entre corridas.
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
            # Borrado + alta en la MISMA conexion y la misma transaccion: el
            # articulo nunca queda a medias aunque el proceso muera aca en el
            # medio. Llamar a index_article() en este punto pedia una segunda
            # conexion al pool que se quedaba esperando el lock del DELETE de
            # esta: deadlock garantizado en cada reindexado.
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM chunks WHERE article_id = %s AND collection = %s",
                    (article_id, collection),
                )
            _write_chunks(
                conn, article_id, title, content, source, genre, url,
                content_hash, collection, meta=meta, chunks=chunks,
            )
            return "reindexed"

        _write_chunks(
            conn, article_id, title, content, source, genre, url,
            content_hash, collection, meta=meta, chunks=chunks,
        )
        return "indexed"
