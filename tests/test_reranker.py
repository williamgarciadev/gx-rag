"""Unit tests for the Cross-Encoder reranking module."""

from unittest.mock import MagicMock, patch
import pytest

from src.vectorstore.reranker import rerank


@pytest.mark.unit
def test_rerank_empty_candidates():
    assert rerank("test query", [], top_k=5) == []
    assert rerank("", [{"title": "t", "content": "c", "score": 0.5}], top_k=5) == [
        {"title": "t", "content": "c", "score": 0.5}
    ]


@pytest.mark.unit
def test_rerank_reorders_by_cross_encoder_score():
    candidates = [
        {"doc_id": "c1", "title": "Doc 1", "content": "general content", "score": 0.05},
        {"doc_id": "c2", "title": "Doc 2", "content": "highly relevant content", "score": 0.02},
        {"doc_id": "c3", "title": "Doc 3", "content": "somewhat relevant content", "score": 0.01},
    ]

    mock_model = MagicMock()
    # Mock scores: candidate 2 gets highest score (5.2), candidate 3 gets 2.1, candidate 1 gets 0.4
    mock_model.predict.return_value = [0.4, 5.2, 2.1]

    with patch("src.vectorstore.reranker.get_reranker", return_value=mock_model):
        results = rerank("relevant content", candidates, top_k=2)

    assert len(results) == 2
    # First should now be c2
    assert results[0]["doc_id"] == "c2"
    assert results[0]["score"] == 5.2
    assert results[0]["rrf_score"] == 0.02
    assert results[0]["reranked"] is True

    # Second should be c3
    assert results[1]["doc_id"] == "c3"
    assert results[1]["score"] == 2.1
    assert results[1]["rrf_score"] == 0.01


@pytest.mark.unit
def test_rerank_handles_model_failure_gracefully():
    candidates = [{"doc_id": "c1", "title": "Doc 1", "content": "content", "score": 0.05}]

    with patch("src.vectorstore.reranker.get_reranker", side_effect=RuntimeError("Model error")):
        results = rerank("query", candidates, top_k=5)

    assert len(results) == 1
    assert results[0]["doc_id"] == "c1"
    assert results[0].get("rerank_fallback") is True
