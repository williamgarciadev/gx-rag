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
# parse_text_files — Word plain-text exports (Bantotal functional manual)
# ─────────────────────────────────────────────────────────────────────────────


def test_parse_text_files_normalizes_bare_cr_and_strips_bom(tmp_path):
    """Guards the exact regression that silently ate a previous ingest.

    These exports are UTF-8 WITH BOM and terminate paragraphs with a bare CR.
    Read as bytes they collapse into one line with no blank line anywhere, and
    the recursive splitter then cuts on arbitrary spaces instead of paragraphs —
    no error, just worse chunks.
    """
    p = tmp_path / "Procesos Batch.txt"
    p.write_bytes("﻿Titulo\r\r\rParrafo uno\rParrafo dos\r".encode("utf-8"))

    doc = next(bl.parse_text_files([p], id_prefix="bt/test"))

    assert "\r" not in doc.content
    assert "﻿" not in doc.content
    assert "\n\n" in doc.content  # consecutive CRs survive as a paragraph break
    assert doc.content.startswith("Titulo")


def test_parse_text_files_derives_identity_from_filename(tmp_path):
    # The first line is a TOC heading, so a first-line title would be wrong here.
    p = tmp_path / "Plan de Cuentas.txt"
    p.write_bytes(
        "﻿CONTENIDO DEL CAPÍTULO DE \"PLAN DE CUENTAS\"\r"
        "Definicion Pág. 3\r"
        "Rubros Pág. 7\r"
        "\rEl rubro se compone del codigo contable base.\r".encode("utf-8")
    )

    doc = next(
        bl.parse_text_files(
            [p], id_prefix="bt/manual-funcional/", title_prefix="Manual — "
        )
    )

    assert doc.source_key == "bt/manual-funcional/plan-de-cuentas"
    assert doc.title == "Manual — Plan de Cuentas"
    assert doc.page_id == "Plan de Cuentas.txt"
    assert doc.url == str(p.resolve())  # load_dump maps this to source AND url
    # The chapter's own TOC is dropped: it repeats every section title and would
    # match any query about the chapter.
    assert "Pág. 3" not in doc.content
    assert "CONTENIDO DEL" not in doc.content
    assert doc.content.startswith("El rubro se compone")


def test_parse_text_files_keeps_content_without_a_toc(tmp_path):
    """A chapter with no TOC must come back untouched — the stripper is opt-in."""
    p = tmp_path / "Cadena de Cierre.txt"
    p.write_bytes("﻿CADENA DE CIERRE\rEl programa es el PP014.\r".encode("utf-8"))

    doc = next(bl.parse_text_files([p], id_prefix="bt/test"))

    assert "CADENA DE CIERRE" in doc.content
    assert "PP014" in doc.content


def test_parse_text_files_converts_word_artifacts(tmp_path):
    p = tmp_path / "Sector Contabilidad.txt"
    p.write_bytes("﻿col a\x07col b\rpagina uno\x0cpagina dos\r".encode("utf-8"))

    doc = next(bl.parse_text_files([p], id_prefix="bt/test"))

    assert "\x07" not in doc.content and "\t" in doc.content
    # Form feeds become paragraph breaks, never page numbers: measured, they do
    # not line up with the "Pág. N" the chapter's own index reports.
    assert "\x0c" not in doc.content
    assert "pagina uno\n\npagina dos" in doc.content


def test_split_index_files_separates_volume_indexes(tmp_path):
    names = ["IndiceTomo1.txt", "IndiceTomo2.txt", "Prestamos.txt", "DPF's.txt"]
    for n in names:
        (tmp_path / n).write_text("x", encoding="utf-8")

    content, indexes = bl.split_index_files(tmp_path.glob("*.txt"))

    assert [p.name for p in indexes] == ["IndiceTomo1.txt", "IndiceTomo2.txt"]
    assert {p.name for p in content} == {"Prestamos.txt", "DPF's.txt"}


def test_parse_text_files_skips_files_that_normalize_to_nothing(tmp_path):
    (tmp_path / "Vacio.txt").write_bytes("﻿\r\r\x0c\r".encode("utf-8"))
    (tmp_path / "Bueno.txt").write_bytes("﻿contenido real\r".encode("utf-8"))

    docs = list(bl.parse_text_files(sorted(tmp_path.glob("*.txt")), id_prefix="bt/t"))

    assert [d.page_id for d in docs] == ["Bueno.txt"]


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


