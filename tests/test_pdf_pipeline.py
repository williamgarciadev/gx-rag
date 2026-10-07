"""Unit tests for the Bantotal manual PDF ingestion pipeline."""

from pathlib import Path

import pytest

from src.ingestion.pdf_pipeline import (
    PDFDocumentMetadata,
    PDFLayoutExtractor,
    PDFPageContent,
    PDFSynthesizer,
    PDFTable,
    extract_bt_identifiers,
)


@pytest.mark.unit
def test_pdf_table_to_markdown():
    table = PDFTable(
        page_number=1,
        headers=["Campo", "Tipo", "Descripción"],
        rows=[
            ["Aotope", "Num(3)", "Tipo de Operación"],
            ["Aosuc", "Num(4)", "Sucursal | Cuenta"],
        ],
    )
    md = table.to_markdown()

    assert "| Campo | Tipo | Descripción |" in md
    assert "| --- | --- | --- |" in md
    assert "| Aotope | Num(3) | Tipo de Operación |" in md
    # Test pipe escaping inside cell
    assert "Sucursal \\| Cuenta" in md


@pytest.mark.unit
def test_extract_bt_identifiers_in_manual():
    text = (
        "El programa B701 actualiza la tabla FSD011 de saldos. "
        "Adicionalmente, se ejecuta la rutina RTE02 sobre la tabla de control FST900."
    )
    programs, tables = extract_bt_identifiers(text)
    assert "B701" in programs
    assert "RTE02" in programs
    assert "FSD011" in tables
    assert "FST900" in tables


@pytest.mark.unit
def test_pdf_synthesizer_generate_markdown():
    meta = PDFDocumentMetadata(
        source_file="Manual_Prestamos_B701.pdf",
        title="Manual de Préstamos — Alta B701",
        module="Préstamos",
        total_pages=2,
        programs=["B701"],
        tables=["FSD011"],
    )

    table = PDFTable(
        page_number=1,
        headers=["Parámetro", "Valor"],
        rows=[["Tasa", "Anual Fija"]],
    )

    pages = [
        PDFPageContent(
            page_number=1,
            text_blocks=["Paso 1: Ingreso de solicitud en B701."],
            tables=[table],
            programs=["B701"],
            tables_detected=["FSD011"],
        ),
        PDFPageContent(
            page_number=2,
            text_blocks=["Paso 2: Validación contable y autorización."],
            tables=[],
            programs=[],
            tables_detected=[],
        ),
    ]

    markdown = PDFSynthesizer.generate_markdown(meta, pages)

    assert markdown.startswith("---")
    assert 'source_type: "manual_pdf"' in markdown
    assert 'title: "Manual de Préstamos — Alta B701"' in markdown
    assert 'module: "Préstamos"' in markdown
    assert "## Resumen Ejecutivo de Negocio" in markdown
    assert "### [Página 1] - Transacción `B701`" in markdown
    assert "Paso 1: Ingreso de solicitud en B701." in markdown
    assert "| Parámetro | Valor |" in markdown
    assert "### [Página 2]" in markdown


@pytest.mark.unit
def test_pdf_layout_extractor_with_synthetic_pdf(tmp_path: Path):
    import pymupdf

    pdf_path = tmp_path / "test_manual.pdf"
    doc = pymupdf.open()
    page = doc.new_page()

    # Draw text in the body
    page.insert_text((50, 150), "Manual Operativo Bantotal. Transacción B701 sobre tabla FST011.")
    doc.save(str(pdf_path))
    doc.close()

    extractor = PDFLayoutExtractor()
    pages = extractor.extract_document(pdf_path)

    assert len(pages) == 1
    assert "B701" in pages[0].programs
    assert "FST011" in pages[0].tables_detected
    assert any("Transacción B701" in b for b in pages[0].text_blocks)
