"""Behavioral tests for whole-article reassembly (collections.get_article)."""

import pytest

from src.vectorstore import collections

pytestmark = pytest.mark.unit


class _FakeCol:
    def __init__(self, result):
        self._result = result

    def get(self, ids=None, where=None, include=None):
        return self._result


class _FakeClient:
    def __init__(self, col):
        self._col = col

    def get_or_create_collection(self, name):
        return self._col


def _wire(monkeypatch, result):
    monkeypatch.setattr(
        collections, "get_client", lambda: _FakeClient(_FakeCol(result))
    )


def test_get_article_reassembles_chunks_in_order(monkeypatch):
    # Chunks arrive out of order; get_article must stitch them by chunk_index.
    _wire(
        monkeypatch,
        {
            "ids": ["art_c1", "art_c0", "art_c2"],
            "documents": ["second", "first", "third"],
            "metadatas": [
                {
                    "title": "T",
                    "url": "http://x",
                    "source": "http://x",
                    "content_hash": "H",
                    "chunk_index": 1,
                },
                {
                    "title": "T",
                    "url": "http://x",
                    "source": "http://x",
                    "content_hash": "H",
                    "chunk_index": 0,
                },
                {
                    "title": "T",
                    "url": "http://x",
                    "source": "http://x",
                    "content_hash": "H",
                    "chunk_index": 2,
                },
            ],
        },
    )

    art = collections.get_article("art", collection="gx_docs")

    assert art is not None
    assert art["id"] == "art"
    assert art["chunks"] == 3
    assert art["content"] == "first\n\nsecond\n\nthird"  # reassembled in order
    assert art["title"] == "T"
    assert art["url"] == "http://x"
    assert art["content_hash"] == "H"


def test_get_article_returns_none_when_absent(monkeypatch):
    _wire(monkeypatch, {"ids": [], "documents": [], "metadatas": []})

    assert collections.get_article("nope", collection="gx_docs") is None
