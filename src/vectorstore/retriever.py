"""
Hybrid retriever — combina búsqueda semántica (dense) + BM25 + reranker.
"""

import logging
from typing import Literal

from src.vectorstore.chroma_client import get_client
from src.vectorstore import embeddings as emb

log = logging.getLogger(__name__)


def hybrid_search(
    query: str,
    collection: str = "gx_docs",
    top_k: int = 5,
) -> list[dict]:
    """
    Búsqueda híbrida: dense embedding + BM25, con reranking.
    Por ahora retorna results de ChromaDB con búsqueda densa.
    """
    client = get_client()
    col = client.get_or_create_collection(name=collection)

    # Dense embedding query
    query_embedding = emb.embed(query)

    results = col.query(
        query_embeddings=[query_embedding.tolist()],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]

    if not docs:
        return []

    # Convertir distancia a score (ChromaDB usa distancia euclidiana/L2)
    # Score = 1 - distancia normalizada (aproximación)
    output = []
    for doc, meta, dist in zip(docs, metas, dists):
        # Score normalizado entre 0 y 1
        score = max(0.0, 1.0 - (dist / 2.0)) if dist is not None else 0.0
        output.append({
            "id": meta.get("id", ""),
            "title": meta.get("title", ""),
            "content": doc,
            "source": meta.get("source", ""),
            "score": score,
        })

    # Ordenar por score descendente
    output.sort(key=lambda x: x["score"], reverse=True)
    return output[:top_k]
