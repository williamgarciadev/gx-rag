"""Behavioral tests for the bulk dump loader (Workstream A).

Parsers run on small inline fixture strings (no real files, no models).
load_dump and the dedup helper are tested with injected fakes so no embedding
model or ChromaDB is loaded.
"""

import pytest

from src.ingestion import bulk_loader as bl

pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────────────
# Parsers
# ─────────────────────────────────────────────────────────────────────────────

WWP_FIXTURE = """Iniciando extração completa do site: https://docs.workwithplus.com/wiki?1639
================================================================================

--- URL: https://docs.workwithplus.com/wiki?1639,Toc%3AWorkWithPlus+for+Web, ---

WorkWithPlus for Web
is the most used product within the GeneXus community.
Page Id
1639

================================================================================

--- URL: https://docs.workwithplus.com/wiki?1641,Toc%3AData+Visualization, ---

Data Visualization
Improves data visualization.
Page Id
1641

================================================================================
"""

GENEXUS_FIXTURE = """

--- INÍCIO DO CONTEÚDO DE: 10000.html ---

GeneXus IDE Toolbox Newest Version GeneXus IDE Toolbox It contains the controls.

--- FIM DO CONTEÚDO DE: 10000.html ---



--- INÍCIO DO CONTEÚDO DE: 6850.html ---

Default rule Newest Version Assigns a default value to an attribute.

--- FIM DO CONTEÚDO DE: 6850.html ---
"""


def test_parse_wwp_extracts_url_title_content():
    docs = list(bl.parse_wwp_dump(WWP_FIXTURE))

    assert len(docs) == 2
    first = docs[0]
    assert (
        first.url
        == "https://docs.workwithplus.com/wiki?1639,Toc%3AWorkWithPlus+for+Web,"
    )
    assert first.source_key == first.url
    assert first.page_id == "1639"
    assert first.title == "WorkWithPlus for Web"
    # The 'Page Id / 1639' trailer and the ==== separators are stripped from content.
    assert "Page Id" not in first.content
    assert "====" not in first.content
    assert "most used product" in first.content


def test_parse_genexus_reconstructs_canonical_url():
    docs = list(bl.parse_genexus_dump(GENEXUS_FIXTURE))

    assert len(docs) == 2
    d = {doc.page_id: doc for doc in docs}
    assert set(d) == {"10000", "6850"}
    assert d["6850"].url == "https://wiki.genexus.com/commwiki/servlet/wiki?6850"
    assert d["6850"].source_key == d["6850"].url
    assert "Default rule" in d["6850"].title
    assert "default value" in d["6850"].content


# ─────────────────────────────────────────────────────────────────────────────
# load_dump — tallying, with an injected index function (no Chroma, no embeddings)
# ─────────────────────────────────────────────────────────────────────────────


def _doc(i):
    return bl.RawDoc(
        source_key=f"u{i}", title=f"t{i}", content=f"c{i}", url=f"u{i}", page_id=str(i)
    )


def test_load_dump_tallies_index_statuses():
    docs = [_doc(1), _doc(2), _doc(3)]
    statuses = iter(["indexed", "skipped", "reindexed"])

    def fake_index(
        article_id, title, content, source, genre, url, content_hash, collection, **kw
    ):
        # dumps must land in retrieval only, never the syntax catalog
        assert genre == "guide"
        assert collection == "gx_docs"
        return next(statuses)

    report = bl.load_dump(
        docs, collection="gx_docs", index_fn=fake_index, hash_fn=lambda t: "h"
    )

    assert report.total == 3
    assert report.indexed == 1
    assert report.skipped == 1
    assert report.reindexed == 1


def test_load_dump_forwards_per_doc_metadata_and_breadcrumb():
    # RawDoc.page_id used to die here: load_dump never passed it on, so the page
    # a Bantotal answer must cite only survived inside the title string.
    doc = bl.RawDoc(
        source_key="u1",
        title="t1",
        content="c1",
        url="u1",
        page_id="41",
        meta={"manual": "MDI-10201", "page": 41},
        chunk_prefix="[MDI-10201 · p.41]",
    )
    seen = {}

    def fake_index(*args, **kw):
        seen.update(kw)
        return "indexed"

    bl.load_dump([doc], index_fn=fake_index, hash_fn=lambda t: "h")

    assert seen["extra_meta"] == {"manual": "MDI-10201", "page": 41}
    assert seen["chunk_prefix"] == "[MDI-10201 · p.41]"


# ─────────────────────────────────────────────────────────────────────────────
# Dedup helper (indexer.reindex_if_changed) — decision logic isolated from embeddings
# ─────────────────────────────────────────────────────────────────────────────


class _FakeCol:
    def __init__(self, existing):
        self._existing = existing
        self.deleted = None

    def get(self, where=None, include=None):
        return self._existing

    def delete(self, ids=None):
        self.deleted = ids


class _FakeClient:
    def __init__(self, col):
        self._col = col

    def get_or_create_collection(self, name):
        return self._col


@pytest.fixture
def _indexer(monkeypatch):
    from src.vectorstore import indexer

    calls = []
    monkeypatch.setattr(
        indexer, "index_article", lambda *a, **k: calls.append((a, k)) or ["id_c0"]
    )
    return indexer, calls


def _wire_client(monkeypatch, indexer, col):
    monkeypatch.setattr(indexer, "get_client", lambda: _FakeClient(col))


def test_reindex_skips_when_hash_unchanged(_indexer, monkeypatch):
    indexer, calls = _indexer
    col = _FakeCol({"ids": ["a_c0"], "metadatas": [{"content_hash": "H"}]})
    _wire_client(monkeypatch, indexer, col)

    status = indexer.reindex_if_changed(
        "a", "t", "content", "src", "guide", "url", "H", collection="gx_docs"
    )

    assert status == "skipped"
    assert calls == []  # never re-embedded
    assert col.deleted is None


def test_reindex_forwards_metadata_and_breadcrumb_to_the_indexer(_indexer, monkeypatch):
    indexer, calls = _indexer
    col = _FakeCol({"ids": [], "metadatas": []})
    _wire_client(monkeypatch, indexer, col)

    indexer.reindex_if_changed(
        "a", "t", "content", "src", "guide", "url", "H",
        collection="bt_docs",
        extra_meta={"manual": "MDI-10201"},
        chunk_prefix="[MDI-10201 · p.41]",
    )

    (_, kwargs), = calls
    assert kwargs["extra_meta"] == {"manual": "MDI-10201"}
    assert kwargs["chunk_prefix"] == "[MDI-10201 · p.41]"


def test_reindex_reindexes_when_hash_differs(_indexer, monkeypatch):
    indexer, calls = _indexer
    col = _FakeCol({"ids": ["a_c0", "a_c1"], "metadatas": [{"content_hash": "OLD"}]})
    _wire_client(monkeypatch, indexer, col)

    status = indexer.reindex_if_changed(
        "a", "t", "content", "src", "guide", "url", "NEW", collection="gx_docs"
    )

    assert status == "reindexed"
    assert col.deleted == ["a_c0", "a_c1"]  # stale chunks removed first
    assert len(calls) == 1


def test_reindex_indexes_when_absent(_indexer, monkeypatch):
    indexer, calls = _indexer
    col = _FakeCol({"ids": [], "metadatas": []})
    _wire_client(monkeypatch, indexer, col)

    status = indexer.reindex_if_changed(
        "a", "t", "content", "src", "guide", "url", "H", collection="gx_docs"
    )

    assert status == "indexed"
    assert len(calls) == 1
    assert col.deleted is None
