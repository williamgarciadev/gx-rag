"""Tests for the local-file loader (PDF + Markdown → RawDoc).

These are integration tests over real PDFs: they check the *mapping* from
`bt_pdf_extract.Chapter` onto the RawDoc contract — one doc per chapter, page
range provenance, manual metadata, breadcrumb. The extraction rules themselves
(furniture, TOC, headings, tables) are unit-tested in `test_bt_pdf_extract.py`
against the IR, without PDFs.

Fixtures are written in Helvetica, so the font-based heading detector cannot
fire and the extractor falls back to size-over-body — the same path the ETP
manuals take. Headings are therefore drawn at `HEAD` and prose at `BODY`, and a
chapter only opens on an "N.0 …" heading.
"""

import fitz  # pymupdf
import pytest

pytestmark = pytest.mark.integration

from src.ingestion.local_loader import iter_supported_files, parse_local_docs

BODY = 8
HEAD = 14

# A chapter must clear `_MIN_CHAPTER_WORDS` (25) to be ingested at all, so the
# fixtures need real prose rather than a token line.
PROSE = (
    "El sistema de prestamos registra las operaciones de credito y su seguimiento "
    "contable, incluyendo el devengamiento de intereses, la mora y las "
    "reestructuraciones aprobadas por el comite de credito."
)
MORE_PROSE = (
    "La clase de tasa determina el calculo aplicado en cada cuota y se parametriza "
    "por moneda, plazo y linea de credito segun la tabla vigente en el sistema."
)


def _write_manual_pdf(path, pages: list[list[tuple]]) -> None:
    """Write a PDF with a cover followed by `pages`.

    Every real manual has a cover and the extractor drops page 1, so a fixture
    without one would silently lose its first body page. Each box is
    `(x0, y0, x1, y1, text)` with an optional trailing font size.
    """
    doc = fitz.open()
    cover = doc.new_page()
    cover.insert_textbox(fitz.Rect(60, 300, 500, 360), "Manual Bantotal", fontsize=20)
    for boxes in pages:
        page = doc.new_page()
        for x0, y0, x1, y1, text, *size in boxes:
            page.insert_textbox(
                fitz.Rect(x0, y0, x1, y1), text, fontsize=size[0] if size else BODY
            )
    doc.save(str(path))
    doc.close()


def _chapter(heading: str, prose: str = PROSE) -> list[tuple]:
    """A one-page chapter: heading above, prose below."""
    return [
        (60, 60, 520, 90, heading, HEAD),
        (60, 120, 520, 300, prose, BODY),
    ]


def _wide_chapter_pages() -> list[list[tuple]]:
    """One "1.0" chapter over 12 pages, with a "1.1" to be split on.

    Past `_MAX_CITABLE_PAGES` (10) the extractor cuts on the chapter's own
    sub-headings, so a fixture for that behaviour has to be genuinely long.
    """
    return (
        [_chapter("1.0 Parametrizacion")]
        + [[(60, 120, 520, 300, MORE_PROSE, BODY)] for _ in range(9)]
        + [_chapter("1.1 Comisiones")]
    )


def test_pdf_yields_one_doc_per_chapter_with_the_page_it_came_from(tmp_path):
    pdf = tmp_path / "MDI-10201-GL-V3R1.20 - Sistema de Prestamos.pdf"
    _write_manual_pdf(pdf, [_chapter("1.0 Objetivo"), _chapter("2.0 Alcance", MORE_PROSE)])

    docs = list(parse_local_docs([pdf]))

    assert [d.page_id for d in docs] == ["2", "3"]
    assert [d.meta["chapter"] for d in docs] == [1, 2]
    assert "1.0 Objetivo" in docs[0].title
    assert "2.0 Alcance" in docs[1].title
    assert docs[0].source_key != docs[1].source_key


def test_a_chapter_crossing_a_page_break_reports_the_whole_range(tmp_path):
    # This is the reason the unit is the chapter and not the page: the prose
    # continues on the next page and must stay in one doc.
    pdf = tmp_path / "MDI-10201-GL-V3R1.20 - Sistema de Prestamos.pdf"
    _write_manual_pdf(
        pdf,
        [
            _chapter("1.0 Objetivo"),
            [(60, 120, 520, 300, MORE_PROSE, BODY)],
        ],
    )

    (doc,) = parse_local_docs([pdf])

    assert doc.page_id == "2-3"
    assert (doc.meta["page_start"], doc.meta["page_end"]) == (2, 3)
    assert "devengamiento" in doc.content
    assert "clase de tasa" in doc.content


def test_the_cover_page_is_not_ingested(tmp_path):
    pdf = tmp_path / "MDI-10201-GL-V3R1.20 - Sistema de Prestamos.pdf"
    _write_manual_pdf(pdf, [_chapter("1.0 Objetivo")])

    (doc,) = parse_local_docs([pdf])

    assert "Manual Bantotal" not in doc.content


def test_meta_carries_manual_code_version_and_page_range(tmp_path):
    pdf = tmp_path / "MDI-10201-GL-V3R1.20 - Sistema de Prestamos.pdf"
    _write_manual_pdf(pdf, [_chapter("1.0 Objetivo")])

    (doc,) = parse_local_docs([pdf])

    assert doc.meta["manual"] == "MDI-10201"
    assert doc.meta["manual_version"] == "V3R1.20"
    assert doc.meta["manual_title"] == "Sistema de Prestamos"
    assert doc.meta["page"] == "2"
    assert doc.meta["section"] == "1.0 Objetivo"
    assert doc.meta["doc_type"] == "manual_pdf_chapter"


def test_a_wide_chapter_is_split_into_citable_sections(tmp_path):
    # 12 pages under one "1.0" heading: cited whole it would say "pp.2-13".
    pdf = tmp_path / "MDI-10201-GL-V3R1.20 - Sistema de Prestamos.pdf"
    _write_manual_pdf(pdf, _wide_chapter_pages())

    docs = list(parse_local_docs([pdf]))

    assert len(docs) > 1
    assert all(d.meta["page_end"] - d.meta["page_start"] + 1 <= 10 for d in docs)
    assert all(d.meta["section"].startswith(("1.0", "1.1")) for d in docs)
    # Every piece still names its chapter, so a chunk situates on its own.
    assert all("1.0 Parametrizacion" in d.chunk_prefix for d in docs)


def test_a_stub_section_is_folded_backwards_not_dropped(tmp_path):
    # Splitting leaves headings with one line under them. In a parametrisation
    # manual that line is a whole fact, so it is folded into the previous piece.
    pdf = tmp_path / "MDI-10201-GL-V3R1.20 - Sistema de Prestamos.pdf"
    _write_manual_pdf(
        pdf,
        _wide_chapter_pages()
        + [
            [
                (60, 60, 520, 90, "1.2 Ordinal 46", HEAD),
                (60, 120, 520, 160, "Indica si aplica comision por mora.", BODY),
            ]
        ],
    )

    docs = list(parse_local_docs([pdf]))

    assert "Indica si aplica comision por mora." in docs[-1].content
    assert not any(d.meta["section"] == "1.2 Ordinal 46" for d in docs)


def test_a_stub_opening_its_own_chapter_is_kept(tmp_path):
    # Folding it onto the previous chapter would make its citation lie.
    pdf = tmp_path / "MDI-10201-GL-V3R1.20 - Sistema de Prestamos.pdf"
    _write_manual_pdf(
        pdf,
        [
            _chapter("1.0 Objetivo"),
            [(60, 60, 520, 90, "2.0 Continua", HEAD)],
        ],
    )

    docs = list(parse_local_docs([pdf]))

    assert [d.meta["section"] for d in docs] == ["1.0 Objetivo", "2.0 Continua"]


def test_every_bantotal_document_prefix_yields_citation_metadata(tmp_path):
    # Bantotal issues more than manuals: ETP/ECP procedure notes, PRD product
    # catalogues, CAP course material. A prefix left out of the pattern still
    # ingests, silently, with no code or version — so its chunks cannot be
    # cited. ECP is the awkward one: six digits and no language segment.
    stems = {
        "MDI-10110-GL-V3R1.6 - Sistema de Contabilidad": ("MDI-10110", "V3R1.6"),
        "MDU-10115-CO-V3R1.1 - Sistema de Precios": ("MDU-10115", "V3R1.1"),
        "ETP-11600-GL-V3R1.1- Reprogramacion Creditos (HPP1919)": ("ETP-11600", "V3R1.1"),
        "ECP-000302-V3R1.1 - Renovacion Polizas (PJCCA311)": ("ECP-000302", "V3R1.1"),
        "PRD-99000-GL-V2R3.2 - Funcionalidades Bantotal": ("PRD-99000", "V2R3.2"),
        "CAP-10111-GL-V2R2.1 - Conceptos de Contabilidad": ("CAP-10111", "V2R2.1"),
    }
    for stem, (code, version) in stems.items():
        pdf = tmp_path / f"{stem}.pdf"
        _write_manual_pdf(pdf, [_chapter("1.0 Objetivo")])

        (doc,) = parse_local_docs([pdf])

        assert (doc.meta["manual"], doc.meta["manual_version"]) == (code, version)


def test_a_filename_off_convention_still_ingests_without_manual_metadata(tmp_path):
    pdf = tmp_path / "09 - Sistema de Prestamos.pdf"
    _write_manual_pdf(pdf, [_chapter("1.0 Objetivo")])

    (doc,) = parse_local_docs([pdf])

    assert doc.meta["manual"] == ""
    assert "devengamiento" in doc.content


def test_chunk_prefix_situates_the_chunk_for_the_reader(tmp_path):
    # Metadata is for filtering; `content` is what the agent reads. The
    # breadcrumb goes into the text so it also reaches the embedding and BM25.
    pdf = tmp_path / "MDI-10201-GL-V3R1.20 - Sistema de Prestamos.pdf"
    _write_manual_pdf(pdf, [_chapter("1.0 Calculo de Interes de Mora")])

    (doc,) = parse_local_docs([pdf])

    assert doc.chunk_prefix == (
        "[MDI-10201 Sistema de Prestamos · 1.0 Calculo de Interes de Mora · p.2]"
    )


def test_a_parametrisation_table_reaches_the_chunk_as_markdown(tmp_path):
    # The headline gain over page-based extraction: the table survives as a
    # table, header row included, instead of arriving as shredded cell lines.
    pdf = tmp_path / "MDI-10110-GL-V3R1.1 - Parametros.pdf"
    _write_manual_pdf(
        pdf,
        [
            [
                (60, 60, 520, 90, "1.0 Parametros Generales", HEAD),
                (60, 120, 520, 240, PROSE, BODY),
                # Header row: three cells at the same y.
                (100, 280, 140, 300, "Nº", BODY),
                (155, 280, 240, 300, "Concepto", BODY),
                (330, 280, 500, 300, "Descripcion", BODY),
                # One data row, anchored on the short column-0 cell.
                (100, 330, 140, 350, "307", BODY),
                (155, 330, 300, 350, "Correlativo de pais", BODY),
                (330, 330, 520, 360, "Valor por defecto en wfs01300.", BODY),
            ]
        ],
    )

    (doc,) = parse_local_docs([pdf])

    assert "| Nº | Concepto | Descripcion |" in doc.content
    assert "| 307 | Correlativo de pais | Valor por defecto en wfs01300. |" in doc.content


def test_markdown_yields_one_doc_titled_by_its_first_heading(tmp_path):
    md = tmp_path / "Bantotal_Rapidas.md"
    md.write_text("# Consultas rápidas\n\nFSD601 filtra D601co='S'.", encoding="utf-8")

    docs = list(parse_local_docs([md]))

    assert len(docs) == 1
    assert docs[0].title == "Consultas rápidas"
    assert docs[0].page_id == ""
    assert "D601co" in docs[0].content


def test_markdown_without_heading_falls_back_to_filename(tmp_path):
    md = tmp_path / "12_AlturaMora_FST198_FSD011.md"
    md.write_text("Altura de mora se calcula sobre FSD011.", encoding="utf-8")

    (doc,) = parse_local_docs([md])

    assert doc.title == "12_AlturaMora_FST198_FSD011"


def test_directory_sweep_recurses_and_ignores_unsupported_files(tmp_path):
    (tmp_path / "_skipped").mkdir()
    (tmp_path / "manual.md").write_text("x", encoding="utf-8")
    (tmp_path / "_skipped" / "nested.md").write_text("y", encoding="utf-8")
    (tmp_path / "notes.docx").write_text("z", encoding="utf-8")

    found = sorted(p.name for p in iter_supported_files(tmp_path))

    assert found == ["manual.md", "nested.md"]


def test_markdown_with_frontmatter_extracts_title_and_meta(tmp_path):
    md = tmp_path / "manual_test.md"
    content = (
        "---\n"
        "title: \"Manual Bantotal — Cadena de Cierre\"\n"
        "module: \"RTE\"\n"
        "---\n\n"
        "# Titulo Secundario\n\n"
        "Contenido de prueba."
    )
    md.write_text(content, encoding="utf-8")

    (doc,) = parse_local_docs([md])

    assert doc.title == "Manual Bantotal — Cadena de Cierre"
    assert doc.meta.get("module") == "RTE"
    assert doc.meta.get("doc_type") == "markdown"


def test_markdown_with_frontmatter_without_title_extracts_body_heading(tmp_path):
    md = tmp_path / "manual_no_title.md"
    content = (
        "---\n"
        "module: \"RTE\"\n"
        "---\n\n"
        "# Titulo Desde Heading\n\n"
        "Contenido de prueba."
    )
    md.write_text(content, encoding="utf-8")

    (doc,) = parse_local_docs([md])

    assert doc.title == "Titulo Desde Heading"
    assert doc.meta.get("module") == "RTE"

