"""Cross-encoder reranking layer for gx-rag hybrid retrieval.

Implements secondary ranking over top RRF candidates. While RRF (Reciprocal Rank
Fusion) reconciles dense cosine similarity and lexical tsvector ranks without scale
distortion, a cross-encoder model processes query and passage tokens jointly via
full cross-attention, capturing fine-grained semantic relevance and filtering out
lexical false positives.

Lazy-loaded to prevent importing heavy PyTorch/Transformer weights until actually
invoked, preserving fast startup times for CLI tools and unit tests.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config import config

log = logging.getLogger(__name__)

_reranker_instance = None


def get_reranker(model_name: str | None = None):
    """Lazy-load the CrossEncoder singleton."""
    global _reranker_instance
    if _reranker_instance is None:
        target_model = model_name or config.vectorstore.reranker_model
        log.info(f"Loading CrossEncoder reranker model: {target_model}")
        try:
            from sentence_transformers import CrossEncoder

            _reranker_instance = CrossEncoder(target_model)
            log.info("CrossEncoder reranker loaded successfully.")
        except Exception as e:
            log.error(f"Failed to load CrossEncoder ({target_model}): {e}")
            raise
    return _reranker_instance


def rerank(
    query: str,
    candidates: list[dict[str, Any]],
    top_k: int = 5,
    model_name: str | None = None,
) -> list[dict[str, Any]]:
    """Rerank hybrid search candidates using cross-encoder full-attention scoring.

    Parameters:
        query: User search query.
        candidates: List of candidate dictionaries returned by hybrid_search.
        top_k: Number of highest-scoring documents to return.
        model_name: Optional override for the cross-encoder model name.

    Returns:
        A re-ordered list of length at most top_k, where each candidate has:
        - score: New cross-encoder relevance score (float).
        - rrf_score: Preserved original RRF rank fusion score.
    """
    if not candidates or not query.strip():
        return candidates[:top_k]

    try:
        model = get_reranker(model_name)
    except Exception as e:
        log.warning(f"Reranker unavailable ({e}), returning unreranked candidates with fallback tag.")
        fallback = []
        for c in candidates[:top_k]:
            item = dict(c)
            item["rerank_fallback"] = True
            fallback.append(item)
        return fallback

    # Prepare (query, passage) pairs. Include title and snippet for holistic matching.
    pairs = [
        (query, f"{c.get('title', '')}\n\n{c.get('content', '')}".strip())
        for c in candidates
    ]

    try:
        scores = model.predict(pairs)
    except Exception as e:
        log.error(f"Prediction failed in CrossEncoder rerank ({e}), returning unreranked candidates with fallback tag.")
        fallback = []
        for c in candidates[:top_k]:
            item = dict(c)
            item["rerank_fallback"] = True
            fallback.append(item)
        return fallback

    reranked = []
    for candidate, score in zip(candidates, scores):
        item = dict(candidate)
        item["rrf_score"] = item.get("score")
        item["score"] = round(float(score), 6)
        item["reranked"] = True
        reranked.append(item)

    # Sort descending by cross-encoder score
    reranked.sort(key=lambda x: x["score"], reverse=True)
    return reranked[:top_k]
