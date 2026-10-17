"""
Indexador sobre PostgreSQL: chunking, embedding y persistencia.

Una sola entrada de escritura: index_document() (idempotente, borra huérfanos).
reindex_if_changed() es la misma operación reducida a su status, para los
cargadores por lote.

Ventaja concreta del cambio: el reindexado es una transaccion. En ChromaDB el
DELETE de los chunks viejos y el INSERT de los nuevos eran dos operaciones
independientes — si el proceso moria en el medio, el articulo quedaba sin
chunks. Aca o pasan las dos o no pasa ninguna.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.vectorstore import embeddings as emb
from src.vectorstore.chunk_ids import chunk_id
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
        title, content, source, url, genre, content_hash, embedding, meta,
        emb_profile
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (chunk_id) DO UPDATE SET
        collection   = EXCLUDED.collection,
        title        = EXCLUDED.title,
        content      = EXCLUDED.content,
        source       = EXCLUDED.source,
        url          = EXCLUDED.url,
        genre        = EXCLUDED.genre,
        content_hash = EXCLUDED.content_hash,
        embedding    = EXCLUDED.embedding,
        meta         = EXCLUDED.meta,
        emb_profile  = EXCLUDED.emb_profile
"""
# `emb_profile` viaja con el embedding por la misma razón que `collection`: es la
# geometría de ESE vector. Sin él, toda fila nueva caía en el DEFAULT 'raw' de
# sql/005 aunque se hubiera embebido con el perfil e5v1, y la columna que existe
# para que el estado no mienta mentía.
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
    extra_meta: dict | None = None,
    chunk_prefix: str = "",
    chunks: list[tuple[str, str]] | None = None,
) -> list[str]:
    combined_meta = {**(extra_meta or {}), **(meta or {})}
    if chunks is not None:
        kinds = [kind for kind, _ in chunks]
        textos = [texto for _, texto in chunks]
    else:
        textos = _splitter.split_text(content)
        kinds = [None] * len(textos)

    if chunk_prefix:
        textos = [f"{chunk_prefix}\n{texto}" for texto in textos]

    if not textos:
        log.warning("sin chunks para %s", article_id)
        return []

    # El perfil se lee JUNTO con el embedding: es el que embed_passage acaba de usar.
    perfil = emb.EMB_PROFILE
    embs = emb.embed_passage(tuple(textos))
    chunk_ids = [chunk_id(article_id, i) for i in range(len(textos))]

    def _meta_de(i: int) -> str:
        """meta del chunk i, con su chunk_kind si el llamador partio por seccion.

        El chunk_kind viaja en TODOS los sub-chunks de una seccion: si una seccion
        larga se parte en varios, el filtro por chunk_kind tiene que seguir
        encontrandolos a todos.
        """
        if not combined_meta and kinds[i] is None:
            return "{}"
        completa = dict(combined_meta)
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
            perfil,
        )
        for i in range(len(textos))
    ]

    with conn.cursor() as cur:
        cur.executemany(_UPSERT_SQL, rows)

    log.info("indexados %d chunks de %s en %s", len(textos), article_id, collection)
    return chunk_ids


@dataclass(frozen=True)
class IndexResult:
    """status: "indexed" | "reindexed" | "skipped".
    chunk_count: chunks que el artículo tiene en el store al terminar."""

    status: str
    chunk_count: int


def index_document(
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
    extra_meta: dict | None = None,
    chunk_prefix: str = "",
    chunks: list[tuple[str, str]] | None = None,
) -> IndexResult:
    """Indexa de forma idempotente. Es la ÚNICA entrada de escritura del indexer.

    - "skipped":   el artículo ya está ENTERO en esta colección con este hash
    - "reindexed": había chunks (con otro hash o en OTRA colección); se borran
                   todos y se reindexa
    - "indexed":   no había chunks

    El borrado es por article_id, sin filtrar colección: chunk_id es clave
    global, así que un artículo vive en una sola colección. Filtrando por
    colección, moverlo dejaba huérfanos en la vieja los chunks que el upsert no
    pisaba. Y el borrado + alta van en la MISMA transacción: el artículo nunca
    queda a medias aunque el proceso muera en el medio.
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            # GROUP BY y no LIMIT 1: con un solo (colección, hash) el artículo
            # está entero y al día; cualquier mezcla (hashes distintos, o restos
            # en otra colección) se repara reindexando.
            cur.execute(
                "SELECT collection, content_hash, count(*) FROM chunks "
                "WHERE article_id = %s GROUP BY 1, 2",
                (article_id,),
            )
            estado = cur.fetchall()

        if len(estado) == 1 and estado[0][:2] == (collection, content_hash):
            return IndexResult("skipped", estado[0][2])

        if estado:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chunks WHERE article_id = %s", (article_id,))

        ids = _write_chunks(
            conn, article_id, title, content, source, genre, url,
            content_hash, collection, meta=meta, extra_meta=extra_meta,
            chunk_prefix=chunk_prefix, chunks=chunks,
        )
        return IndexResult("reindexed" if estado else "indexed", len(ids))


def reindex_if_changed(*args, **kwargs) -> str:
    """index_document reducido a su status, con la misma firma.

    Lo usan los cargadores por lote (bulk_loader, kb_loader, scrape_sac) y sus
    tests inyectan un index_fn que devuelve el status como texto: para ellos el
    conteo de chunks no aporta nada.
    """
    return index_document(*args, **kwargs).status
