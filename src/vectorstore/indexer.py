"""
Indexador — chunking, embedding y persistencia en ChromaDB.
"""

import logging

from langchain_text_splitters import RecursiveCharacterTextSplitter
from src.vectorstore.chroma_client import get_client
from src.vectorstore import embeddings as emb

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
) -> list[str]:
    """
    Indexa un artículo en ChromaDB.
    Devuelve lista de chunk IDs insertados.
    """
    # Chunking
    chunks = _splitter.split_text(content)
    if not chunks:
        log.warning(f"No chunks generated for {article_id}")
        return []

    # Embeddings
    embs = emb.embed_texts(tuple(chunks))

    # Metadatos por chunk
    metas = [
        {
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

    if existing_ids:
        metas = existing.get("metadatas") or [{}]
        prev_hash = (metas[0] or {}).get("content_hash")
        if prev_hash == content_hash:
            return "skipped"
        col.delete(ids=existing_ids)
        index_article(
            article_id, title, content, source, genre, url, content_hash, collection
        )
        return "reindexed"

    index_article(
        article_id, title, content, source, genre, url, content_hash, collection
    )
    return "indexed"
