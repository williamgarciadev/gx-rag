"""
Behavioral tests for the hybrid retriever (dense + BM25 + RRF).

The ChromaDB collection and the embedding model are faked so these tests
exercise the fusion logic without loading models or a real vector store.
"""

from collections import Counter

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

    def get(self, include=None, limit=None, offset=0):
        # The retriever scans collections in pages: an unbounded get() overflows
        # SQLite's variable limit on a large collection. Honour the window, and
        # return empty past the end so the paging loop terminates.
        page = self._docs[offset : offset + limit if limit else None]
        return {
            "ids": [d[0] for d in page],
            "documents": [d[1] for d in page],
            "metadatas": [d[2] for d in page],
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
    docs = [
        (
            "a",
            "genexus transaction",
            {
                "id": "a",
                "title": "T",
                "source": "s",
                "url": "http://x",
                "content_hash": "H",
            },
        )
    ]
    col = _FakeCollection(docs, dense_order=["a"])
    _wire(monkeypatch, col)

    results = retriever.hybrid_search("genexus", top_k=5)

    assert len(results) == 1
    assert set(results[0]) == {
        "doc_id",
        "id",
        "title",
        "content",
        "source",
        "score",
        "url",
        "content_hash",
        "manual",
        "page",
        "section",
    }
    assert results[0]["id"] == "a"
    assert results[0]["content"] == "genexus transaction"
    # Provenance the agent needs to cite without a second call.
    assert results[0]["url"] == "http://x"
    assert results[0]["content_hash"] == "H"
    # Wiki chunks have no manual provenance; the keys stay present but empty.
    assert (results[0]["manual"], results[0]["page"], results[0]["section"]) == ("", "", "")


def test_manual_provenance_is_surfaced_for_pdf_chunks(monkeypatch):
    # "MDI-10201 p.121, §1.16.1.1" is the citation the consuming KB requires;
    # without these fields it can only be recovered by parsing the title string.
    docs = [
        (
            "m_c0",
            "La tasa de mora se toma de la clase de tasa 2.",
            {
                "id": "file:///m.pdf#page=121",
                "title": "MDI-10201 — p.121",
                "manual": "MDI-10201",
                "page": 121,
                "section": "1.16.1.1 Cálculo de Interés de Mora",
            },
        )
    ]
    col = _FakeCollection(docs, dense_order=["m_c0"])
    _wire(monkeypatch, col)

    (result,) = retriever.hybrid_search("mora clase de tasa", top_k=5)

    assert result["manual"] == "MDI-10201"
    assert result["page"] == 121
    assert result["section"] == "1.16.1.1 Cálculo de Interés de Mora"


def test_min_score_filters_out_low_scoring_results(monkeypatch):
    docs = [("a", "genexus transaction", {"id": "a", "title": "T", "source": "s"})]
    col = _FakeCollection(docs, dense_order=["a"])
    _wire(monkeypatch, col)

    # RRF scores are small (~0.03); an impossibly high floor filters everything.
    assert retriever.hybrid_search("genexus", top_k=5, min_score=1.0) == []
    # Default (no floor) still returns the hit.
    assert len(retriever.hybrid_search("genexus", top_k=5)) == 1


def test_doc_id_is_chroma_chunk_id_not_metadata_id(monkeypatch):
    # The Chroma chunk id ('art_c0') differs from the article id in metadata
    # ('art'). get_document() looks up by the Chroma chunk id, so that is what
    # search_docs must expose for the result to be fetchable afterwards.
    docs = [
        ("art_c0", "genexus transaction", {"id": "art", "title": "T", "source": "s"})
    ]
    col = _FakeCollection(docs, dense_order=["art_c0"])
    _wire(monkeypatch, col)

    result = retriever.hybrid_search("genexus", top_k=5)[0]

    assert result["doc_id"] == "art_c0"  # resolvable by get_document
    assert result["id"] == "art"  # article id, for grouping/provenance


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


def test_bm25_index_rebuilds_when_the_collection_changes(monkeypatch):
    # Asserts on the OUTCOME (the new document is retrievable), not on the cache
    # key. The previous version compared the key to a document count, which
    # pinned the mechanism — and that mechanism was the bug: a rewrite keeping
    # the same count never invalidated. See test_bm25_cache_invalidation.py.
    docs = [("a", "alpha", {"id": "a", "title": "", "source": ""})]
    col = _FakeCollection(docs, dense_order=["a"])
    _wire(monkeypatch, col)
    retriever.hybrid_search("alpha")
    cached_key = retriever._bm25_cache["gx_docs"][0]

    col._docs.append(("b", "beta", {"id": "b", "title": "", "source": ""}))
    results = retriever.hybrid_search("beta")

    assert retriever._bm25_cache["gx_docs"][0] != cached_key
    assert any(r["content"] == "beta" for r in results)


def test_one_article_cannot_fill_the_whole_result_set(monkeypatch):
    # A manual chapter yields dozens of chunks. Before the cap, "interés de mora"
    # returned MDI-10201 §1.10.1.1 three times at top_k=3 and buried the other
    # manuals that had an answer.
    docs = [
        (f"cap_c{i}", f"mora interés cálculo parte {i}", {"id": "cap", "title": "Cap"})
        for i in range(5)
    ] + [
        ("otro_c0", "mora interés en otro manual", {"id": "otro", "title": "Otro"}),
        ("tercero_c0", "mora interés en un tercero", {"id": "tercero", "title": "Tercero"}),
    ]
    col = _FakeCollection(docs, dense_order=[d[0] for d in docs])
    _wire(monkeypatch, col)

    results = retriever.hybrid_search("mora interés", top_k=4)

    counts = Counter(r["id"] for r in results)
    assert counts["cap"] <= retriever._MAX_CHUNKS_PER_ARTICLE
    # The slots freed by the cap go to the other sources, not to nothing.
    assert len(results) == 4
    assert {"otro", "tercero"} <= set(counts)


def test_the_cap_still_allows_an_answer_split_across_two_chunks(monkeypatch):
    docs = [
        ("cap_c0", "la tasa de mora se toma de la clase", {"id": "cap", "title": "Cap"}),
        ("cap_c1", "de tasa 2 según la parametrización", {"id": "cap", "title": "Cap"}),
    ]
    col = _FakeCollection(docs, dense_order=["cap_c0", "cap_c1"])
    _wire(monkeypatch, col)

    results = retriever.hybrid_search("tasa de mora", top_k=5)

    assert [r["doc_id"] for r in results] == ["cap_c0", "cap_c1"]


def test_a_chunk_without_an_article_id_is_capped_on_its_own_id(monkeypatch):
    # Metadata may lack 'id'; falling back to the chunk id keeps each such chunk
    # independent instead of collapsing them all into one pseudo-article.
    docs = [
        ("x_c0", "mora uno", {"title": "X"}),
        ("x_c1", "mora dos", {"title": "X"}),
        ("x_c2", "mora tres", {"title": "X"}),
    ]
    col = _FakeCollection(docs, dense_order=["x_c0", "x_c1", "x_c2"])
    _wire(monkeypatch, col)

    assert len(retriever.hybrid_search("mora", top_k=3)) == 3
