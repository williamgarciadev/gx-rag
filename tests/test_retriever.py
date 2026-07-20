"""
Behavioral tests for the hybrid retriever (dense + BM25 + RRF).

The ChromaDB collection and the embedding model are faked so these tests
exercise the fusion logic without loading models or a real vector store.
"""

import numpy as np
import pytest

from src.vectorstore import retriever

pytestmark = pytest.mark.unit


class _FakeCollection:
    """Minimal stand-in for a ChromaDB collection.

    `dense_order` is the id ranking the dense query returns; BM25 ranking is
    computed for real over the documents by the retriever.
    """

    def __init__(self, docs, dense_order):
        self.name = "gx_docs"
        self._docs = docs  # list of (id, text, meta)
        self._dense_order = dense_order

    def count(self):
        return len(self._docs)

    def get(self, include=None):
        return {
            "ids": [d[0] for d in self._docs],
            "documents": [d[1] for d in self._docs],
            "metadatas": [d[2] for d in self._docs],
        }

    def query(self, query_embeddings, n_results, include):
        return {"ids": [self._dense_order[:n_results]]}


class _FakeClient:
    def __init__(self, col):
        self._col = col

    def get_or_create_collection(self, name):
        return self._col


@pytest.fixture(autouse=True)
def _clear_bm25_cache():
    retriever._bm25_cache.clear()
    yield
    retriever._bm25_cache.clear()


def _wire(monkeypatch, col):
    monkeypatch.setattr(retriever, "get_client", lambda: _FakeClient(col))
    monkeypatch.setattr(retriever.emb, "embed", lambda q: np.zeros(3))


def test_empty_collection_returns_empty(monkeypatch):
    col = _FakeCollection(docs=[], dense_order=[])
    _wire(monkeypatch, col)

    assert retriever.hybrid_search("anything") == []


def test_result_shape(monkeypatch):
    docs = [("a", "genexus transaction", {"id": "a", "title": "T", "source": "s"})]
    col = _FakeCollection(docs, dense_order=["a"])
    _wire(monkeypatch, col)

    results = retriever.hybrid_search("genexus", top_k=5)

    assert len(results) == 1
    assert set(results[0]) == {"id", "title", "content", "source", "score"}
    assert results[0]["id"] == "a"
    assert results[0]["content"] == "genexus transaction"


def test_bm25_rescues_document_dense_missed(monkeypatch):
    # 'b' is the only doc containing 'loan' and is absent from the dense ranking.
    docs = [
        ("a", "genexus transaction procedure", {"id": "a", "title": "A", "source": ""}),
        ("b", "banking loan module", {"id": "b", "title": "B", "source": ""}),
        ("c", "vector search index", {"id": "c", "title": "C", "source": ""}),
    ]
    col = _FakeCollection(docs, dense_order=["a", "c"])  # dense never returns 'b'
    _wire(monkeypatch, col)

    ids = [r["id"] for r in retriever.hybrid_search("loan", top_k=3)]

    # Hybrid value: the lexically-relevant doc surfaces even though dense missed it.
    assert "b" in ids


def test_bm25_index_rebuilds_when_count_changes(monkeypatch):
    docs = [("a", "alpha", {"id": "a", "title": "", "source": ""})]
    col = _FakeCollection(docs, dense_order=["a"])
    _wire(monkeypatch, col)
    retriever.hybrid_search("alpha")
    assert retriever._bm25_cache["gx_docs"][0] == 1  # cached at count=1

    col._docs.append(("b", "beta", {"id": "b", "title": "", "source": ""}))
    retriever.hybrid_search("beta")
    assert retriever._bm25_cache["gx_docs"][0] == 2  # rebuilt at count=2
