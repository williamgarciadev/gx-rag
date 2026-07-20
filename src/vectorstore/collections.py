"""
Colecciones ChromaDB — helpers para obtener y manipular documentos.
"""

import logging
from typing import Any

from src.vectorstore.chroma_client import get_client

log = logging.getLogger(__name__)


def get_document(doc_id: str, collection: str = "gx_docs") -> dict[str, Any] | None:
    """Obtiene un documento por su ID."""
    client = get_client()
    col = client.get_or_create_collection(name=collection)
    try:
        result = col.get(ids=[doc_id], include=["documents", "metadatas"])
        docs = result.get("documents", [])
        metas = result.get("metadatas", [])
        if not docs:
            return None
        meta = metas[0] if metas else {}
        return {
            "id": doc_id,
            "content": docs[0],
            "title": meta.get("title", ""),
            "source": meta.get("source", ""),
            "collection": collection,
            "content_hash": meta.get("content_hash", ""),
        }
    except Exception as exc:
        log.error(f"get_document error: {exc}")
        return None


def get_article(article_id: str, collection: str = "gx_docs") -> dict[str, Any] | None:
    """Reassemble a whole article from its chunks.

    search_docs / get_document operate at chunk granularity (an 800-char slice).
    An agent that wants full grounding needs the entire document; this stitches
    all chunks sharing the article id (metadata "id") back together in
    chunk_index order.
    """
    client = get_client()
    col = client.get_or_create_collection(name=collection)
    try:
        result = col.get(where={"id": article_id}, include=["documents", "metadatas"])
        docs = result.get("documents") or []
        if not docs:
            return None
        metas = result.get("metadatas") or []
        ordered = sorted(
            zip(docs, metas), key=lambda dm: (dm[1] or {}).get("chunk_index", 0)
        )
        meta0 = ordered[0][1] or {}
        return {
            "id": article_id,
            "content": "\n\n".join(d for d, _ in ordered),
            "title": meta0.get("title", ""),
            "source": meta0.get("source", ""),
            "url": meta0.get("url", ""),
            "collection": collection,
            "content_hash": meta0.get("content_hash", ""),
            "chunks": len(ordered),
        }
    except Exception as exc:
        log.error(f"get_article error: {exc}")
        return None


def collection_exists(name: str) -> bool:
    """Verifica si una colección existe."""
    client = get_client()
    try:
        client.get_collection(name=name)
        return True
    except Exception:
        return False


def list_collections() -> list[str]:
    """Lista todas las colecciones."""
    client = get_client()
    return [c.name for c in client.list_collections()]
