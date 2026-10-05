"""Every corpus write has to announce itself.

The retriever's BM25 cache is invalidated by a corpus version that the WRITER
stamps. If the indexer forgets to stamp it, the mechanism looks correct in
retriever tests and does nothing in production — the reader would keep serving
a stale lexical index and nothing would surface it.

So the wiring is pinned here, on the writer's side, rather than assumed.
"""

import pytest

from src.vectorstore import indexer

pytestmark = pytest.mark.unit


class _FakeCollection:
    def __init__(self):
        self.added = []
        self.deleted = []
        self.existing = {"ids": [], "metadatas": []}

    def add(self, embeddings, documents, metadatas, ids):
        self.added.extend(ids)

    def get(self, where=None, include=None, ids=None):
        return self.existing

    def delete(self, ids):
        self.deleted.extend(ids)


@pytest.fixture
def wired(monkeypatch):
    """Indexer with a fake store and a recorded bump, no model and no Chroma."""
    col = _FakeCollection()
    bumps = []
    monkeypatch.setattr(
        indexer, "get_client", lambda: type("C", (), {"get_or_create_collection": lambda self, name: col})()
    )
    monkeypatch.setattr(indexer.emb, "embed_texts", lambda texts: _zeros(len(texts)))
    monkeypatch.setattr(indexer, "bump_corpus_version", lambda: bumps.append(1))
    return col, bumps


def _zeros(n):
    import numpy as np

    return np.zeros((n, 3))


def _index(**over):
    args = dict(
        article_id="a",
        title="T",
        content="contenido de prueba suficientemente largo",
        source="s",
        genre="guide",
        url="u",
        content_hash="h",
    )
    args.update(over)
    return args


def test_indexing_an_article_announces_the_write(wired):
    _, bumps = wired

    indexer.index_article(**_index())

    assert bumps == [1]


def test_a_reindex_announces_the_write(wired):
    col, bumps = wired
    col.existing = {"ids": ["a_c0"], "metadatas": [{"content_hash": "viejo"}]}

    status = indexer.reindex_if_changed(**_index(content_hash="nuevo"))

    assert status == "reindexed"
    assert bumps  # the rewrite that the count check could not see


def test_a_skipped_reindex_does_not_announce(wired):
    # Nothing changed, so invalidating would throw away a valid index for free.
    col, bumps = wired
    col.existing = {"ids": ["a_c0"], "metadatas": [{"content_hash": "h"}]}

    status = indexer.reindex_if_changed(**_index(content_hash="h"))

    assert status == "skipped"
    assert bumps == []
