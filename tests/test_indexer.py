"""Tests for chunking, per-chunk metadata and the situating breadcrumb.

Embeddings and ChromaDB are faked: what matters here is what gets *stored* per
chunk, which is what a consuming agent later reads and cites.
"""

import numpy as np
import pytest

from src.vectorstore import indexer


class _CapturingCol:
    def __init__(self):
        self.added = None

    def add(self, **kwargs):
        self.added = kwargs


class _Client:
    def __init__(self, col):
        self._col = col

    def get_or_create_collection(self, name):
        return self._col


@pytest.fixture
def col(monkeypatch):
    c = _CapturingCol()
    monkeypatch.setattr(indexer, "get_client", lambda: _Client(c))
    monkeypatch.setattr(
        indexer.emb, "embed_texts", lambda texts: np.zeros((len(texts), 2))
    )
    return c


def _index(col, content, **kw):
    indexer.index_article(
        "file:///m.pdf#page=41",
        "MDI-10201 — p.41",
        content,
        "file:///m.pdf",
        "guide",
        "file:///m.pdf",
        "HASH",
        collection="bt_docs",
        **kw,
    )
    return col.added


def test_breadcrumb_is_prepended_to_every_chunk_not_only_the_first(col):
    # Prefixing the article text before splitting would only situate chunk 0;
    # every chunk is retrieved on its own, so every chunk needs the header.
    long_content = "Rubro contable imputable de la operacion. " * 60

    added = _index(col, long_content, chunk_prefix="[MDI-10201 · 1.16 Mora · p.41]")

    assert len(added["documents"]) > 1
    assert all(d.startswith("[MDI-10201 · 1.16 Mora · p.41]") for d in added["documents"])


def test_chunks_are_unprefixed_when_no_breadcrumb_is_given(col):
    added = _index(col, "Rubro contable imputable")

    assert added["documents"] == ["Rubro contable imputable"]


def test_extra_meta_lands_on_every_chunk(col):
    long_content = "Altura de mora sobre el capital de la cuota adeudada. " * 40

    added = _index(col, long_content, extra_meta={"manual": "MDI-10201", "page": 41})

    assert len(added["metadatas"]) > 1
    assert all(m["manual"] == "MDI-10201" for m in added["metadatas"])
    assert all(m["page"] == 41 for m in added["metadatas"])


def test_extra_meta_cannot_overwrite_structural_metadata(col):
    added = _index(col, "corto", extra_meta={"title": "otro", "chunk_index": 99})

    (meta,) = added["metadatas"]
    assert meta["title"] == "MDI-10201 — p.41"
    assert meta["chunk_index"] == 0
