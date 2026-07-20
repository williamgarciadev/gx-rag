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
