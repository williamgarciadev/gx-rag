"""The BM25 cache must notice a rewrite, not just a resize.

`_get_bm25` keyed its cache on `col.count()`. But `reindex_if_changed` deletes
an article's chunks and re-adds them under the same deterministic ids, so an
edit that yields the same number of chunks leaves the count untouched — and the
cached lexical index keeps serving the DELETED text. Silent, and locally (where
ingestion happens constantly) the common case rather than the edge case.

The fix is a corpus version stamped by the WRITER, not a scan by the reader. A
first attempt hashed every chunk's (id, content_hash) on each query: correct,
and 3.5s per query on gx_docs against a 0.22s warm budget. Detecting a change
must not cost more than the rebuild it avoids — so the writer announces, and the
reader does one stat.
"""

import numpy as np
import pytest

from src.vectorstore import retriever

pytestmark = pytest.mark.unit


class _FakeCollection:
    def __init__(self, docs):
        self.name = "gx_docs"
        self._docs = docs

    def count(self):
        return len(self._docs)

    def get(self, include=None, limit=None, offset=0, where=None):
        page = self._docs[offset : offset + limit if limit else None]
        return {
            "ids": [d[0] for d in page],
            "documents": [d[1] for d in page],
            "metadatas": [d[2] for d in page],
        }

    def query(self, query_embeddings, n_results, include):
        return {"ids": [[d[0] for d in self._docs[:n_results]]]}


class _FakeClient:
    def __init__(self, col):
        self._col = col

    def get_or_create_collection(self, name):
        return self._col


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Point the corpus-version marker at a temp file, never at data/."""
    retriever._bm25_cache.clear()
    monkeypatch.setattr(retriever, "_VERSION_FILE", tmp_path / ".corpus-version")
    yield
    retriever._bm25_cache.clear()


def _wire(monkeypatch, col):
    monkeypatch.setattr(retriever, "get_client", lambda: _FakeClient(col))
    monkeypatch.setattr(retriever.emb, "embed", lambda q: np.zeros(3))


def _meta(article):
    return {"id": article, "title": "T", "source": ""}


def test_a_rewrite_with_the_same_chunk_count_invalidates_the_cache(monkeypatch):
    col = _FakeCollection([("a_c0", "contenido viejo", _meta("a"))])
    _wire(monkeypatch, col)
    retriever.hybrid_search("viejo")  # populates the cache

    # What reindex_if_changed does: same article, same chunk count, new text —
    # and it announces the write.
    col._docs[:] = [("a_c0", "contenido nuevo", _meta("a"))]
    retriever.bump_corpus_version()

    results = retriever.hybrid_search("nuevo")

    assert [r["content"] for r in results] == ["contenido nuevo"]


def test_the_stale_text_is_no_longer_retrievable_after_a_rewrite(monkeypatch):
    col = _FakeCollection([("a_c0", "mora clase de tasa dos", _meta("a"))])
    _wire(monkeypatch, col)
    retriever.hybrid_search("mora")

    col._docs[:] = [("a_c0", "tema completamente distinto", _meta("a"))]
    retriever.bump_corpus_version()

    contents = [r["content"] for r in retriever.hybrid_search("mora clase tasa")]
    assert "mora clase de tasa dos" not in contents


def test_an_unchanged_collection_still_reuses_the_cached_index(monkeypatch):
    # The cache has to keep earning its place: rebuilding BM25 over 22k chunks
    # costs seconds, so invalidating on every query is its own regression.
    col = _FakeCollection([("a_c0", "alfa", _meta("a"))])
    _wire(monkeypatch, col)
    retriever.hybrid_search("alfa")
    first = retriever._bm25_cache["gx_docs"]

    retriever.hybrid_search("alfa")

    assert retriever._bm25_cache["gx_docs"] is first


def test_adding_a_chunk_invalidates_even_without_an_announcement(monkeypatch):
    # Belt and braces: a writer that bypasses the indexer still changes the
    # count, and the count is free to read.
    col = _FakeCollection([("a_c0", "alfa", _meta("a"))])
    _wire(monkeypatch, col)
    retriever.hybrid_search("alfa")

    col._docs.append(("b_c0", "beta", _meta("b")))
    results = retriever.hybrid_search("beta")

    assert any(r["content"] == "beta" for r in results)


def test_reading_the_version_does_not_require_the_marker_to_exist(monkeypatch):
    # First run on a fresh checkout: no marker yet, and retrieval must work.
    col = _FakeCollection([("a_c0", "alfa", _meta("a"))])
    _wire(monkeypatch, col)

    assert retriever.hybrid_search("alfa")


def test_each_bump_produces_a_distinct_version():
    retriever.bump_corpus_version()
    first = retriever._corpus_version()
    retriever.bump_corpus_version()

    assert retriever._corpus_version() != first
