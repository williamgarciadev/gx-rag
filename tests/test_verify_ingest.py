"""Tests de `scripts.verify_ingest`, la verificación posterior a una ingesta.

El pipeline no avisa cuando pierde texto: el 2026-10-07 tres documentos entraron
con un `OK — indexed=N` y les faltaba una sección, una página o una línea. Este
script mide la pérdida comparando el PDF crudo contra lo extraído; si su
aritmética se equivoca, informa "sin pérdida" sobre un documento incompleto.
"""

import pytest

from scripts.verify_ingest import (
    lonely_furniture,
    lost_words,
    scattered_toc_pages,
    source_patterns,
    uncovered_pages,
)


@pytest.mark.unit
def test_lonely_furniture_flags_a_footer_seen_on_a_single_page():
    # A real footer repeats on every page; a line discarded as footer on ONE
    # page is likely content — the cross-reference bug of 2026-10-07.
    seen = {
        "MDI-10110-GL-V3R1.6 - Sistema de Contabilidad, oct.-24": {2, 3, 4},
        "ver el documento MDU-10110-GL-V2R3 – Sistema de Contabilidad.": {55},
    }
    assert lonely_furniture(seen, page_count=4) == ["ver el documento MDU-10110-GL-V2R3 – Sistema de Contabilidad."]


@pytest.mark.unit
def test_lonely_furniture_flags_a_cross_reference_repeated_on_a_few_pages():
    # MDI-10110 repeats the same cross-reference on pages 15 and 23: "one page
    # only" missed it when run against the pre-fix extractor. A footer shows on
    # nearly every page; anything under half of them is worth a look.
    seen = {
        "MDI-10110-GL-V3R1.6 - Sistema de Contabilidad, oct.-24": set(range(2, 153)),
        "correspondiente MDI-10109-GL-V2R3.1- Sistema de Seguridad).": {15, 23},
    }
    assert lonely_furniture(seen, page_count=152) == [
        "correspondiente MDI-10109-GL-V2R3.1- Sistema de Seguridad)."
    ]


@pytest.mark.unit
def test_lonely_furniture_treats_page_numbers_as_one_recurring_footer():
    # "Página 10 de 80" differs on every page only by its digits: comparing
    # raw text flagged all 79 of them in the first real run.
    seen = {f"Página {n} de 80": {n} for n in range(2, 81)}
    assert lonely_furniture(seen, page_count=80) == []


@pytest.mark.unit
def test_lost_words_reports_what_the_raw_text_has_and_the_extraction_lacks():
    raw = "Como configuración previa, mencionada en el manual de pagos"
    extracted = "manual de pagos"
    assert lost_words(raw, extracted) == {"como": 1, "configuración": 1, "previa": 1, "mencionada": 1}


@pytest.mark.unit
def test_lost_words_counts_repetitions_not_just_presence():
    # A word present once in the extraction but twice in the PDF still lost
    # one occurrence — e.g. a whole paragraph dropped whose words appear elsewhere.
    assert lost_words("tasa tasa tasa", "tasa") == {"tasa": 2}


@pytest.mark.unit
def test_lost_words_ignores_case_and_short_tokens():
    # Tokens under 4 chars ("de", "el", "la") are noise for this signal.
    assert lost_words("De la TASA", "tasa") == {}


@pytest.mark.unit
def test_uncovered_pages_expands_ranges_and_skips_the_cover():
    # Page 1 is the cover and is never ingested by design: reporting it would
    # make every document look incomplete.
    assert uncovered_pages(["3-5", "7", ""], page_count=8) == [2, 6, 8]


@pytest.mark.unit
def test_scattered_toc_pages_flags_index_pages_after_the_opening_block():
    # The verifier excludes index pages from coverage, and it learns which ones
    # they are from the extractor's own is_toc_page — so a content page the
    # extractor mistakes for an index was invisible: MDI-10206 reported "páginas
    # sin cubrir: ninguna" with 51 table pages dropped. An index is one block at
    # the start; a lone "index" page at p.21 or p.303 is content until proven
    # otherwise.
    assert scattered_toc_pages([2, 3, 4, 5, 21, 23, 303]) == [21, 23, 303]


@pytest.mark.unit
def test_scattered_toc_pages_accepts_a_single_contiguous_index():
    assert scattered_toc_pages([2, 3, 4]) == []
    assert scattered_toc_pages([]) == []


@pytest.mark.unit
def test_source_patterns_match_the_uri_the_indexer_writes(tmp_path):
    # The indexer keys a PDF's chunks as '<uri>#ch=N' and a Markdown's as the
    # bare uri. Spaces are percent-encoded there: matching on the plain
    # filename returned 0 for a document with 110 chunks (2026-10-07).
    path = tmp_path / "Sistema de Cash Management.pdf"
    exact, chapters = source_patterns(path)
    assert exact == path.as_uri()
    assert "%20" in exact
    assert chapters.endswith("#%")


@pytest.mark.unit
def test_source_patterns_escape_like_wildcards_in_the_uri(tmp_path):
    # '%20' inside the uri is a literal in the stored source; unescaped, LIKE
    # reads its '%' as a wildcard and the prefix match stops being exact.
    # '_' is a LIKE wildcard too (and tmp_path is full of them).
    path = tmp_path / "a b_c.pdf"
    _, chapters = source_patterns(path)
    assert r"a\%20b\_c.pdf#%" in chapters
    assert "a%20b" not in chapters.replace(r"\%", "")
