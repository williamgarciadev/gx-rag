"""
Indexador — chunking, embedding y persistencia en ChromaDB.
"""

import logging

from langchain_text_splitters import RecursiveCharacterTextSplitter
from src.vectorstore.chroma_client import get_client
from src.vectorstore import embeddings as emb
# The retriever caches a BM25 index and cannot afford to detect changes by
# scanning; the writer stamps a version instead. See retriever.bump_corpus_version.
from src.vectorstore.retriever import bump_corpus_version

log = logging.getLogger(__name__)

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=["\n## ", "\n### ", "\n\n", "\n", " "],
    keep_separator=True,
)


def index_article(
    article_id: str,
    title: str,
    content: str,
    source: str,
    genre: str,
    url: str,
    content_hash: str,
    collection: str = "gx_docs",
    extra_meta: dict | None = None,
    chunk_prefix: str | None = None,
) -> list[str]:
    """
    Indexa un artículo en ChromaDB.
    Devuelve lista de chunk IDs insertados.

    `extra_meta` añade metadata por chunk (solo str/int/float/bool — ChromaDB
    rechaza el resto); las claves estructurales tienen prioridad y no se pueden
    sobreescribir desde ahí.

    `chunk_prefix` es un breadcrumb que se antepone al texto de **cada** chunk
    (p. ej. `[MDI-10201 Sistema de Préstamos · 1.16.1.1 Mora · p.121]`). Va
    después del split a propósito: prefijar el artículo completo solo situaría
    al chunk 0, y cada chunk se recupera por separado. Al quedar en el texto
    entra también al embedding y al índice BM25.
    """
    # Chunking
    chunks = _splitter.split_text(content)
    if not chunks:
        log.warning(f"No chunks generated for {article_id}")
        return []

    if chunk_prefix:
        chunks = [f"{chunk_prefix}\n{chunk}" for chunk in chunks]

    # Embeddings
    embs = emb.embed_texts(tuple(chunks))

    # Metadatos por chunk
    metas = [
        {
            **(extra_meta or {}),
            "id": article_id,
            "title": title,
            "source": source,
            "url": url,
            "genre": genre,
            "content_hash": content_hash,
            "chunk_index": i,
        }
        for i in range(len(chunks))
    ]

    # IDs por chunk
    chunk_ids = [f"{article_id}_c{i}" for i in range(len(chunks))]

    # Persistir en ChromaDB
    client = get_client()
    col = client.get_or_create_collection(name=collection)
    col.add(
        embeddings=embs.tolist(),
        documents=chunks,
        metadatas=metas,
        ids=chunk_ids,
    )

    # Announce BEFORE returning: a caller that indexes and immediately searches
    # (the /scrape endpoint does exactly that) must not read a stale index.
    bump_corpus_version()

    log.info(f"Indexed {len(chunks)} chunks for {article_id} in {collection}")
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
    extra_meta: dict | None = None,
    chunk_prefix: str | None = None,
) -> str:
    """Index an article idempotently, keyed by content_hash.

    Returns one of:
      - "skipped":   chunks already exist with the same content_hash (no re-embed)
      - "reindexed": chunks existed with a different hash; stale chunks deleted and re-added
      - "indexed":   no prior chunks; freshly indexed

    Makes bulk re-runs safe: without this, re-ingesting would collide on the
    deterministic chunk ids ("{article_id}_c{i}").
    """
    client = get_client()
    col = client.get_or_create_collection(name=collection)
    existing = col.get(where={"id": article_id}, include=["metadatas"])
    existing_ids = existing.get("ids", []) or []
    passthrough = dict(extra_meta=extra_meta, chunk_prefix=chunk_prefix)

    if existing_ids:
        metas = existing.get("metadatas") or [{}]
        prev_hash = (metas[0] or {}).get("content_hash")
        if prev_hash == content_hash:
            return "skipped"
        col.delete(ids=existing_ids)
        index_article(
            article_id, title, content, source, genre, url, content_hash, collection,
            **passthrough,
        )
        return "reindexed"

    index_article(
        article_id, title, content, source, genre, url, content_hash, collection,
        **passthrough,
    )
    return "indexed"
