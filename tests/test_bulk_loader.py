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
        article_id, title, content, source, genre, url, content_hash, collection
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


# ─────────────────────────────────────────────────────────────────────────────
# Dedup helper (indexer.reindex_if_changed) — decision logic isolated from embeddings
# ─────────────────────────────────────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._conn.sentencias.append(" ".join(sql.split()))

    def fetchone(self):
        # Única consulta que lee: el content_hash del artículo. None = no existe.
        return self._conn.fila


class _FakeConn:
    """Conexión mínima con la forma que usa pg_indexer: with pool.connection()
    as conn, luego with conn.cursor() as cur."""

    def __init__(self, fila):
        self.fila = fila
        self.sentencias: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self)


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def connection(self):
        return self._conn


@pytest.fixture
def _indexer(monkeypatch):
    """pg_indexer con index_article mockeado: acá se prueba la DECISIÓN
    (skipped/reindexed/indexed), no el embedding ni el SQL."""
    from src.vectorstore import pg_indexer

    calls = []
    # Se parchea _write_chunks, no index_article: reindex_if_changed escribe por
    # ahí para compartir la conexión (y evitar el deadlock del DELETE + INSERT).
    monkeypatch.setattr(
        pg_indexer, "_write_chunks", lambda *a, **k: calls.append((a, k)) or ["id_c0"]
    )
    return pg_indexer, calls


def _wire_pool(monkeypatch, indexer, fila):
    conn = _FakeConn(fila)
    monkeypatch.setattr(indexer, "get_pool", lambda: _FakePool(conn))
    return conn


def test_reindex_skips_when_hash_unchanged(_indexer, monkeypatch):
    indexer, calls = _indexer
    conn = _wire_pool(monkeypatch, indexer, fila=("H",))

    status = indexer.reindex_if_changed(
        "a", "t", "content", "src", "guide", "url", "H", collection="gx_docs"
    )

    assert status == "skipped"
    assert calls == []  # never re-embedded
    assert not any("DELETE" in s for s in conn.sentencias)


def test_reindex_reindexes_when_hash_differs(_indexer, monkeypatch):
    indexer, calls = _indexer
    conn = _wire_pool(monkeypatch, indexer, fila=("OLD",))

    status = indexer.reindex_if_changed(
        "a", "t", "content", "src", "guide", "url", "NEW", collection="gx_docs"
    )

    assert status == "reindexed"
    assert any("DELETE" in s for s in conn.sentencias)  # stale chunks removed first
    assert len(calls) == 1


def test_reindex_indexes_when_absent(_indexer, monkeypatch):
    """Sin fila previa no hay nada que borrar: se indexa y listo."""
    indexer, calls = _indexer
    conn = _wire_pool(monkeypatch, indexer, fila=None)

    status = indexer.reindex_if_changed(
        "a", "t", "content", "src", "guide", "url", "H", collection="gx_docs"
    )

    assert status == "indexed"
    assert len(calls) == 1
    assert not any("DELETE" in s for s in conn.sentencias)


