"""Unit tests for `src/ingestion/bt_pdf_extract.py`.

The fitz-dependent layer is thin; everything here exercises the PURE layer
(IR -> Markdown) with synthetic lines/tables, mirroring what first-hand
inspection found in the real corpus (see module docstring):

- furniture: "Página N de M", copyright, doc-code line, "Documento Privado",
  bare page numbers near the edges
- TOC pages: dot-leader lines
- headings: Demi font + numbering, number-only line + title continuation,
  unnumbered 20pt chapters ("Introducción"), ETP-style size fallback
- tables: ruled TableIR emission, borderless x-band rebuild, multi-page
  continuation, prose ending a table
- prose: lowercase continuation joins, field lists ("X = value") never join
- page spans: the 1-based PDF range each chapter was drawn from

Page numbers are 1-based throughout, cover included — `page(..., number=1)` is
the cover and is always skipped, so body pages start at 2.
"""

import pytest

from src.ingestion.bt_pdf_extract import (
    LineIR,
    PageIR,
    chapters_from_pages,
    is_furniture,
    is_toc_page,
)

BOOK = "FranklinGothic-Book"
DEMI = "FranklinGothic-Demi"


def L(
    text: str,
    *,
    size: float = 11.0,
    font: str = BOOK,
    demi: bool = False,
    x: float = 90.0,
    y: float | None = None,
    x1: float | None = None,
) -> LineIR:
    if demi:
        font = DEMI
    # y=-1 means "auto-assign sequentially in page()" — real PDF lines never
    # share the same y unless they are cells of the same visual row.
    y0 = y if y is not None else -1.0
    # page=0 is a placeholder: page() stamps the real number, so a line cannot
    # end up claiming a page its own PageIR does not have.
    return LineIR(
        page=0,
        x0=x,
        y0=y0,
        x1=x1 if x1 is not None else x + 200,
        y1=y0 + 12,
        text=text,
        font=font,
        size=size,
        is_demi=demi,
    )


def page(lines, number=2, height=842.0) -> PageIR:
    """A body page. Defaults to 2 because page 1 is the cover and is skipped."""
    lines = list(lines)
    for i, line in enumerate(lines):
        line.page = number
        if line.y0 < 0:
            line.y0 = 100.0 + i * 14
            line.y1 = line.y0 + 12
    return PageIR(number=number, height=height, lines=lines)


# ─────────────────────────────────────────────────────────────────────────────
# is_furniture
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestFurniture:
    def test_pagina_pattern(self) -> None:
        assert is_furniture(L("Página 42 de 225"), 842)

    def test_copyright_pattern(self) -> None:
        assert is_furniture(L("Copyright © Todos los derechos reservados a Bantotal"), 842)
        assert is_furniture(L("Copyright © Todos los derechos reservados a Bantotal."), 842)

    def test_doc_code_line(self) -> None:
        # The doc-code footer sits 50-88pt above the bottom edge on all 3.337
        # occurrences measured across the corpus (2026-10-07); y=760 is in it.
        assert is_furniture(L("- MDI-10109-GL-V3R1.28-Sistema de Contrapartes, Jul.-26", y=760), 842)
        assert is_furniture(L("MDU-10202-GL-V3R1.4 Sistema de Descuentos, oct.-25", y=760), 842)
        assert is_furniture(L("ETP-10201-GL-V2R3.2 - CAMBIO DE TASA MASIVO – (PPP92021), feb-26", y=760), 842)

    def test_a_body_line_citing_another_manual_is_content(self) -> None:
        # A doc code mid-page is a cross-reference, not a footer. Matching it
        # anywhere dropped the one line naming a prerequisite manual in
        # ETP-TransferenciasACH, and cross-references in MDI-10109, MDI-10110
        # and MDU-10115 (5 lines, all >= 267pt from the bottom edge).
        line = "Como configuración previa, mencionada en el MDI-10308-CO-V3R1.3 - Medios de Pagos"
        assert not is_furniture(L(line, y=137), 842)

    def test_documento_clasificacion(self) -> None:
        assert is_furniture(L("Documento Privado"), 842)
        assert is_furniture(L("Documento Público"), 842)

    def test_bare_page_number_edges(self) -> None:
        # Top-of-page number (MDU-10205 prints it in 11pt body font).
        assert is_furniture(L("6", y=70), 842)
        # Bottom-of-page number.
        assert is_furniture(L("6", y=810), 842)
        # Same digits mid-page are CONTENT (could be a table value).
        assert not is_furniture(L("6", y=400), 842)

    def test_body_text_not_furniture(self) -> None:
        assert not is_furniture(L("El sistema de contrapartes permite"), 842)

    def test_watermark_char_fragments_bottom_band(self) -> None:
        # The "Documento Privado" watermark shatters into 1-char runs (y=786).
        assert is_furniture(L("D", y=786), 842)
        assert is_furniture(L("t", y=786), 842)
        # …but the same chars mid-page are content (e.g. list markers).
        assert not is_furniture(L("D", y=400), 842)

    def test_watermark_rejoined_with_spaces(self) -> None:
        # Spans re-join "t P i" / "D t P i d" when separated by >1pt gaps.
        assert is_furniture(L("t P i", y=786), 842)
        assert is_furniture(L("D t P i d", y=786), 842)
        # Real short words mid-page are NOT furniture.
        assert not is_furniture(L("de", y=400), 842)


# ─────────────────────────────────────────────────────────────────────────────
# is_toc_page
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestTocPage:
    def test_dot_leaders_detected(self) -> None:
        lines = [
            L("1.1 Opciones Generales de Procesos .................... 7"),
            L("1.2 Guías de Procesos ................................ 9"),
            L("1.3 Programas particulares .......................... 13"),
        ]
        assert is_toc_page(lines)

    def test_dots_on_own_line(self) -> None:
        # MDI-10110 style: number/title on one line, dots on the next.
        lines = [
            L("1.1 Modos de Uso"),
            L("................................................................"),
            L("1.2 Funcionamiento"),
            L("................................................................"),
            L("1.3 Controles"),
            L("................................................................"),
        ]
        assert is_toc_page(lines)

    def test_normal_page_not_toc(self) -> None:
        lines = [L("Párrafo normal."), L("Otro párrafo."), L("Uno más.")]
        assert not is_toc_page(lines)

    def test_right_margin_page_refs_without_dot_leaders(self) -> None:
        # MDU-10115 style: the entry and its page number are separate lines and
        # there are no dots at all. What marks it is the column of numbers
        # flush right (x/width = 0.88 on the real page).
        lines = [
            L("2.4.1.1.2 Modificar Pizarra de Tasas", x=90),
            L("39", x=525, x1=537),
            L("2.4.1.1.3 Eliminar Pizarra de Tasas", x=90),
            L("39", x=525, x1=537),
            L("3.0 Monedas – Cotizaciones", x=90),
            L("54", x=525, x1=537),
        ]
        assert is_toc_page(lines, 595.0)

    def test_a_left_hand_number_column_is_not_a_toc(self) -> None:
        # A parametrisation table's Nº column is also bare numbers — but flush
        # LEFT. Treating it as an index would delete the table.
        lines = [
            L("307", x=100, x1=120),
            L("Correlativo de país", x=155),
            L("1301", x=100, x1=124),
            L("RUC de la Empresa", x=155),
            L("2507", x=100, x1=124),
            L("Habilita la visibilidad", x=155),
        ]
        assert not is_toc_page(lines, 595.0)

    def test_a_table_with_a_right_hand_numeric_column_is_not_a_toc(self) -> None:
        # MDI-10109 p.181: real content, 15 bare numbers, some of them at the
        # right margin. Position alone would delete the page — what saves it is
        # that its numbers sit in SEVERAL columns, unlike an index's.
        lines = [
            L("Se debe parametrizar la guía de procesos.", x=90),
            L("1", x=125, x1=132),
            L("14", x=232, x1=244),
            L("39", x=505, x1=517),
            L("2", x=125, x1=132),
            L("15", x=232, x1=244),
            L("40", x=505, x1=517),
            L("3", x=125, x1=132),
            L("16", x=232, x1=244),
            L("41", x=505, x1=517),
        ]
        assert not is_toc_page(lines, 595.0)

    def test_a_table_whose_numeric_columns_are_all_right_hand_is_not_a_toc(self) -> None:
        # MDI-10206 p.21 ("Carga Asociada al Atributo"): the Reporte/Consulta
        # columns sit at x/width 0.75 and 0.84, so every bare number is in the
        # right margin and the page was dropped as an index. 125 pages of 6
        # manuals were lost this way, 120 in the three Originación de Créditos.
        # What an index has and a table does not: its page references never go
        # DOWN in reading order. These go 20, 1, 20, 4, 20, 3.
        lines = [
            L("FECHA Fecha Inicio Solicitud D (8) Carga Reporte.", x=90, y=200),
            L("20", x=448, x1=460, y=200),
            L("1", x=500, x1=506, y=200),
            L("INSTANCIA Instancia N (4) Carga Reporte.", x=90, y=240),
            L("20", x=448, x1=460, y=240),
            L("4", x=500, x1=506, y=240),
            L("USUARIO Usuario C (10) Carga Reporte.", x=90, y=280),
            L("20", x=448, x1=460, y=280),
            L("3", x=500, x1=506, y=280),
        ]
        assert not is_toc_page(lines, 595.0)

    def test_an_index_with_a_stray_page_number_is_still_a_toc(self) -> None:
        # MDI-10102 Comparador de Fecha p.2: a real index whose own printed page
        # number survives as one more bare number, so the column reads
        # 2, 3, 4, 5, 2. Demanding that the references NEVER go down dropped it
        # and leaked the index as content.
        lines = [
            L("Contenido", x=90, y=200),
            L("2", x=530, x1=536, y=200),
            L("Introducción", x=90, y=230),
            L("3", x=530, x1=536, y=230),
            L("Instalación", x=90, y=260),
            L("4", x=530, x1=536, y=260),
            L("Modo de Uso", x=90, y=290),
            L("5", x=530, x1=536, y=290),
            L("2", x=452, x1=458, y=700),
        ]
        assert is_toc_page(lines, 595.0)


# ─────────────────────────────────────────────────────────────────────────────
# Headings and chapters
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestHeadings:
    def test_numbered_heading_levels_by_size_rank(self) -> None:
        # 20pt -> H2 (chapter), 14pt -> H3, 11pt -> H4 (MDI-10109 profile).
        lines = [
            L("1.0 Parametrización General", size=20, demi=True),
            L("Texto del capítulo uno."),
            L("1.1 Opciones Generales", size=14, demi=True),
            L("Texto de la sección."),
            L("1.1.1 Detalle Fino", size=11, demi=True),
            L("Más texto."),
        ]
        chapters = chapters_from_pages([page(lines)])
        assert len(chapters) == 1
        title, md = chapters[0].title, chapters[0].markdown
        assert title == "1.0 Parametrización General"
        assert "# 1.0 Parametrización General" in md
        assert "### 1.1 Opciones Generales" in md
        assert "#### 1.1.1 Detalle Fino" in md

    def test_number_only_heading_merges_title_line(self) -> None:
        # MDI-10110: "1.1" and "Modos de Uso" are separate Demi lines.
        lines = [
            L("1.0", size=20, demi=True),
            L("Mantenimiento Parámetros", size=20, demi=True),
            L("Cuerpo del capítulo."),
            L("1.1", size=14, demi=True),
            L("Modos de Uso", size=14, demi=True),
            L("Cuerpo de la sección."),
        ]
        chapters = chapters_from_pages([page(lines)])
        assert chapters[0][0] == "1.0 Mantenimiento Parámetros"
        assert "### 1.1 Modos de Uso" in chapters[0][1]

    def test_unnumbered_big_heading_is_chapter(self) -> None:
        lines = [
            L("Introducción", size=20, demi=True),
            L("Texto introductorio del manual."),
            L("1.0 Sistema de Descuentos", size=20, demi=True),
            L("Texto del sistema."),
        ]
        chapters = chapters_from_pages([page(lines)])
        assert [c.title for c in chapters] == ["Introducción", "1.0 Sistema de Descuentos"]

    def test_chapter_split_on_x0(self) -> None:
        lines = [
            L("1.0 Primero", size=20, demi=True),
            L("Texto del primer capítulo."),
            L("2.0 Segundo", size=20, demi=True),
            L("Texto del segundo capítulo."),
        ]
        chapters = chapters_from_pages([page(lines)])
        assert [c.title for c in chapters] == ["1.0 Primero", "2.0 Segundo"]
        assert "primer capítulo" in chapters[0][1]
        assert "segundo capítulo" not in chapters[0][1]

    def test_demi_body_size_heading_not_missed(self) -> None:
        # MDU-10205: "1.2.3.1 Eliminar" is Demi at body size (11pt).
        lines = [
            L("1.0 Sistema", size=20, demi=True),
            L("Texto previo al detalle fino."),
            L("1.2.3.1 Eliminar", size=11, demi=True),
            L("Cuerpo del detalle."),
        ]
        chapters = chapters_from_pages([page(lines)])
        assert any("1.2.3.1 Eliminar" in b for b in chapters[0][1].split("\n"))

    def test_etp_fallback_size_only_headings(self) -> None:
        # ETP: no Demi headings at all — 12pt Book lines become H2, no split.
        lines = [
            L("Objetivo", size=12),
            L("Permitir el cambio masivo de la tasa de interés."),
            L("Instrucciones de uso", size=12),
            L("Luego de cargar los registros se debe llamar al programa."),
        ]
        chapters = chapters_from_pages([page(lines)])
        assert len(chapters) == 1
        md = chapters[0][1]
        assert "## Objetivo" in md
        assert "## Instrucciones de uso" in md

    def test_cover_and_toc_pages_skipped(self) -> None:
        cover = page([L("Manual de Instalador", size=20, demi=True)], number=1)
        toc = page(
            [
                L("1.0 Capítulo .............................. 3"),
                L("2.0 Otro .................................. 9"),
                L("3.0 Más .................................. 20"),
            ],
            number=2,
        )
        body_page = page([L("1.0 Capítulo", size=20, demi=True), L("Texto real.")], number=3)
        chapters = chapters_from_pages([cover, toc, body_page])
        assert len(chapters) == 1
        assert chapters[0][0] == "1.0 Capítulo"
        assert "Manual de Instalador" not in chapters[0][1]

    def test_content_after_the_index_on_the_same_page_is_kept(self) -> None:
        # A short document starts its first chapter right below the index.
        # Skipping the page whole lost section 1.0 of "Buenas Prácticas GX16"
        # (FST017 and the Java-only platform rule), measured 2026-10-07.
        shared = page(
            [
                L("Contenido"),
                L("1.0 Todas las plataformas ..................... 2"),
                L("2.0 Manejo de variables ....................... 4"),
                L("3.0 Uso del order ............................. 5"),
                L("1.0 Todas las plataformas generan en java", size=20, demi=True),
                L("Se agregó un nuevo campo en la FST017 para reconocer el DBMS."),
            ],
            number=2,
        )
        chapters = chapters_from_pages([shared])
        assert [c[0] for c in chapters] == ["1.0 Todas las plataformas generan en java"]
        md = chapters[0][1]
        assert "FST017" in md
        assert "....." not in md
        assert "Contenido" not in md

    def test_footer_remnants_below_an_index_are_not_kept(self) -> None:
        # The footer block of an ETP wraps onto lines with no doc code
        # ("Información para Gestión Créditos" / "(PSNG9122)", 45-53pt above
        # the edge). They sit after the last index entry, so "after the index"
        # alone would leak them on every index page with no content.
        toc = page(
            [
                L("1.0 Objetivo ................................... 3"),
                L("2.0 Alcance .................................... 4"),
                L("3.0 Pruebas .................................... 9"),
                L("Información para Gestión Créditos", y=789),
                L("(PSNG9122)", y=797),
            ],
            number=2,
        )
        body_page = page([L("1.0 Objetivo", size=20, demi=True), L("Texto real.")], number=3)
        md = chapters_from_pages([toc, body_page])[0][1]
        assert "PSNG9122" not in md


# ─────────────────────────────────────────────────────────────────────────────
# Bullets, captions, paragraphs
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestProse:
    def test_wingdings_bullet_glyph_plus_text_line(self) -> None:
        # Wingdings "▪" arrives as its own line; the text follows.
        lines = [
            L("▪", font="Wingdings-Regular"),
            L("Cuenta: permite seleccionar la cuenta cliente."),
            L("▪", font="Wingdings-Regular"),
            L("Moneda: refiere a la moneda solicitada."),
        ]
        md = chapters_from_pages([page(lines)])[0][1]
        assert "- Cuenta: permite seleccionar la cuenta cliente." in md
        assert "- Moneda: refiere a la moneda solicitada." in md

    def test_lowercase_continuation_joins(self) -> None:
        lines = [
            L("Para contemplar esto se agregará la fecha de ingreso o"),
            L("modificación de estos a los mantenimientos de teléfonos."),
        ]
        md = chapters_from_pages([page(lines)])[0][1]
        assert "ingreso o modificación" in md

    def test_field_list_never_joins(self) -> None:
        lines = [
            L("Pgcod = 77"),
            L("Axrubr = Código Contable del Préstamo"),
            L("Axsuc = Sucursal del Préstamo"),
        ]
        md = chapters_from_pages([page(lines)])[0][1]
        # Each field stays its own paragraph; never glued to the previous one.
        assert "Pgcod = 77 Axrubr" not in md
        assert "Axrubr = Código Contable del Préstamo Axsuc" not in md

    def test_figure_caption_kept_as_italic(self) -> None:
        lines = [
            L("El panel que se presenta es el siguiente:"),
            L("Fig.1 Pre-Ingreso de Limites de Crédito (programa Hpp9032)", size=8),
        ]
        md = chapters_from_pages([page(lines)])[0][1]
        assert "*Fig.1 Pre-Ingreso de Limites de Crédito (programa Hpp9032)*" in md

    def test_sentence_after_period_not_joined(self) -> None:
        lines = [
            L("Primera oración completa."),
            L("Segunda oración empieza acá."),
        ]
        md = chapters_from_pages([page(lines)])[0][1]
        assert "completa.\n\nSegunda" in md


# ─────────────────────────────────────────────────────────────────────────────
# Tables
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestTables:
    """Region/anchor reconstruction. Geometry mirrors the real corpus: the Nº
    anchor is vertically CENTERED in its row (concept starts above it)."""

    def _fst098_lines(self, *, y_base: float = 100.0) -> list[LineIR]:
        y = y_base
        return [
            L("Nº", x=105, x1=125, y=y),
            L("Concepto", x=155, x1=210, y=y),
            L("Programa", x=244, x1=290, y=y + 6),  # dropped 6pt
            L("Descripción", x=396, x1=460, y=y),
            # Row 307: anchor centered between concept (above) and desc.
            L("Correlativo 1 - País por defecto en wfs01300,", x=133, x1=290, y=y + 40),
            L("307", x=100, x1=120, y=y + 77),
            L("Wfs0130", x=244, x1=285, y=y + 77),
            L("Si se desea que aparezca por defecto el país de", x=312, x1=540, y=y + 46),
            L("la instalación y el tipo de documento.", x=312, x1=500, y=y + 58),
            # Row 1301.
            L("RUC de la Empresa- persona es empleada de la", x=133, x1=290, y=y + 120),
            L("institución", x=133, x1=180, y=y + 132),
            L("1301", x=97, x1=120, y=y + 126),
            L("Para el RUC de la Empresa.", x=312, x1=480, y=y + 108),
        ]

    def test_parameter_table_rebuilt(self) -> None:
        md = chapters_from_pages([page(self._fst098_lines())])[0][1]
        assert "| Nº | Concepto | Programa | Descripción |" in md
        assert "| 307 | Correlativo 1 - País por defecto en wfs01300, | Wfs0130 | Si se desea que aparezca por defecto el país de la instalación y el tipo de documento. |" in md
        assert "| 1301 | RUC de la Empresa- persona es empleada de la institución |  | Para el RUC de la Empresa. |" in md

    def test_rows_stay_contiguous_so_markdown_reads_it_as_a_table(self) -> None:
        # Blocks are joined with a BLANK line, and a blank line between rows
        # stops Markdown treating them as a table — it renders loose paragraphs
        # of pipes instead. The whole table must be one block.
        md = chapters_from_pages([page(self._fst098_lines())])[0][1]
        lines = md.splitlines()
        rows = [i for i, ln in enumerate(lines) if ln.startswith("|")]
        assert rows == list(range(rows[0], rows[0] + len(rows)))

    def test_centered_cells_do_not_invent_extra_columns(self) -> None:
        # MDI-10109's data-structure tables centre their cells, so one column's
        # x0 wanders with the text width (234 to 274 on the real page) and
        # clustering split it in three. The header caps the count at 3.
        lines = [
            L("Nombre del Campo", x=97, x1=170, y=100),
            L("Tipo", x=187, x1=205, y=100),
            L("Descripción", x=273, x1=330, y=100),
            L("sngc11Pais", x=112, x1=165, y=130),
            L("N ( 3.0 )", x=180, x1=215, y=130),
            L("sngc11 País", x=271, x1=330, y=130),
            L("sngc11NEst", x=109, x1=163, y=160),
            L("C ( 50 )", x=181, x1=213, y=160),
            L("sngc11 Nombre Establecimiento", x=234, x1=390, y=160),
            L("sngc11Cmb1", x=110, x1=166, y=190),
            L("N ( 4.0 )", x=180, x1=215, y=190),
            L("Código combo auxiliar", x=258, x1=370, y=190),
        ]
        md = chapters_from_pages([page(lines)])[0][1]
        assert "| Nombre del Campo | Tipo | Descripción |" in md
        assert "| sngc11NEst | C ( 50 ) | sngc11 Nombre Establecimiento |" in md
        assert "Col " not in md

    def test_header_labels_map_by_order_when_the_counts_match(self) -> None:
        # Headers are centred over their column, data is left-aligned, so
        # nearest-x put two labels in one band and left another empty:
        # "Nº | Col 2 | Concepto Programa | Descripción" in MDI-10109's agenda.
        lines = [
            L("Nº", x=105, x1=118, y=100),
            L("Concepto", x=250, x1=295, y=100),
            L("Programa", x=258, x1=305, y=112),
            L("Descripción", x=320, x1=380, y=100),
            L("2902", x=100, x1=124, y=150),
            L("Campos obligatorios", x=155, x1=250, y=150),
            L("HAGEN01", x=240, x1=290, y=150),
            L("Si se desea marcarlos en rojo.", x=312, x1=470, y=150),
        ]
        md = chapters_from_pages([page(lines)])[0][1]
        assert "| Nº | Concepto | Programa | Descripción |" in md

    def test_dropped_header_cell_joins_label(self) -> None:
        # "Programa" sits 6pt below its row-mates — still header, not a row.
        md = chapters_from_pages([page(self._fst098_lines())])[0][1]
        assert "| Nº | Concepto | Programa | Descripción |" in md
        assert "Programa | Wfs0130" not in md

    def test_wrapped_header_cells_merge(self) -> None:
        # "En la" + "Aplicación:" (next visual row) -> "En la Aplicación:".
        lines = [
            L("Nº", x=106, x1=126, y=100),
            L("Concepto", x=155, x1=215, y=100),
            L("En la", x=240, x1=265, y=100),
            L("Descripción", x=396, x1=460, y=100),
            L("Aplicación:", x=240, x1=295, y=112),
            L("2507", x=95, x1=120, y=140),
            L("Habilita la visibilidad", x=133, x1=240, y=140),
            L("Valor N", x=240, x1=278, y=140),
            L("Para indicar en el panel.", x=292, x1=460, y=140),
        ]
        md = chapters_from_pages([page(lines)])[0][1]
        assert "| Nº | Concepto | En la Aplicación: | Descripción |" in md
        assert "| 2507 | Habilita la visibilidad | Valor N | Para indicar en el panel. |" in md

    def test_table_continuation_across_pages(self) -> None:
        p1 = page(self._fst098_lines(), number=2)
        p2_lines = [
            L("Motivos de baja de cuenta", x=133, x1=250, y=100),
            L("1335", x=99, x1=122, y=108),
            L("Descripción del motivo.", x=312, x1=470, y=100),
        ]
        p2 = page(p2_lines, number=3)
        md = chapters_from_pages([p1, p2])[0][1]
        assert "| 1335 | Motivos de baja de cuenta |  | Descripción del motivo. |" in md
        assert md.count("| Nº | Concepto | Programa | Descripción |") == 1

    def test_prose_ends_table(self) -> None:
        lines = self._fst098_lines() + [
            L("Este párrafo ya es prosa larga fuera de la tabla y cierra la estructura por completo.", x=90, x1=540, y=400),
        ]
        md = chapters_from_pages([page(lines)])[0][1]
        assert md.count("| Nº | Concepto | Programa | Descripción |") == 1
        assert "Este párrafo ya es prosa larga" in md
        assert "prosa larga |" not in md

    def test_heading_ends_table(self) -> None:
        lines = self._fst098_lines() + [
            L("1.2 Guías de Procesos", size=14, demi=True, y=400),
            L("Texto de la nueva sección.", y=430),
        ]
        md = chapters_from_pages([page(lines)])[0][1]
        # Only one heading size in this synthetic doc -> rank H2.
        assert "## 1.2 Guías de Procesos" in md
        assert "Guías de Procesos |" not in md

    def test_pipe_escaped_in_cells(self) -> None:
        lines = [
            L("Nº", x=105, x1=125, y=100),
            L("Descripción", x=292, x1=360, y=100),
            L("307", x=100, x1=120, y=130),
            L("valor a | b mezclado", x=292, x1=420, y=130),
        ]
        md = chapters_from_pages([page(lines)])[0][1]
        assert "valor a \\| b mezclado" in md

    def test_false_header_falls_back_to_prose(self) -> None:
        # Vocab words in a row but no anchors at all -> not a table.
        lines = [
            L("Concepto", x=155, x1=215, y=100),
            L("Descripción", x=292, x1=360, y=100),
            L("Este renglón larguísimo no tiene anclas numéricas ni estructura de tabla.", x=90, x1=540, y=140),
        ]
        md = chapters_from_pages([page(lines)])[0][1]
        assert "| Concepto |" not in md
        assert "renglón larguísimo" in md

    def test_data_row_mimicking_header_does_not_open_table(self) -> None:
        # "179 | Habilita Nombre reducido | Valor N" contains 'Nombre' and
        # 'Valor' (vocab hits) — the col0 cell must NOT be vocab, or this
        # would become a phantom header and shatter the surrounding table.
        lines = [
            L("Nº", x=106, x1=126, y=80),
            L("Concepto", x=155, x1=215, y=80),
            L("Descripción", x=292, x1=360, y=80),
            L("307", x=100, x1=120, y=110),
            L("Concepto inicial", x=133, x1=235, y=110),
            L("Descripción inicial.", x=292, x1=460, y=110),
            L("179", x=102, x1=125, y=140),
            L("Habilita Nombre reducido", x=136, x1=290, y=140),
            L("Descripción dos.", x=292, x1=410, y=140),
            L("471", x=97, x1=120, y=170),
            L("Cat. Comercial defecto CTES", x=133, x1=295, y=170),
            L("Descripción tres.", x=292, x1=415, y=170),
        ]
        md = chapters_from_pages([page(lines)])[0][1]
        # The "179" row stays INSIDE the same table; only one header.
        assert md.count("| Nº | Concepto | Descripción |") == 1
        assert "| 307 | Concepto inicial | Descripción inicial. |" in md
        assert "| 179 | Habilita Nombre reducido | Descripción dos. |" in md
        assert "| 471 | Cat. Comercial defecto CTES | Descripción tres. |" in md

    def test_merged_col0_and_concept_split_into_separate_cells(self) -> None:
        # "23570 Hab. integración" arrives as one line spanning col0+col1
        # because PyMuPDF joined adjacent spans — split it so col0 stays a
        # clean anchor and the concept lands in column 1.
        lines = [
            L("Nº", x=106, x1=126, y=100),
            L("Concepto", x=155, x1=215, y=100),
            L("Descripción", x=292, x1=360, y=100),
            L("23570 Hab. integración", x=95, x1=240, y=140),
            L("% menor 100", x=133, x1=210, y=155),
            L("Para integrar porcentajes.", x=292, x1=460, y=140),
        ]
        md = chapters_from_pages([page(lines)])[0][1]
        assert "| 23570 | Hab. integración % menor 100 | Para integrar porcentajes. |" in md


# ─────────────────────────────────────────────────────────────────────────────
# Whole-doc edge cases
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestEdgeCases:
    def test_no_headings_single_untitled_chapter(self) -> None:
        lines = [L("Todo es prosa sin estructura alguna."), L("Y sigue siendo prosa.")]
        chapters = chapters_from_pages([page(lines)])
        assert len(chapters) == 1
        assert chapters[0][0] == ""
        assert "prosa" in chapters[0][1]

    def test_empty_doc_returns_nothing(self) -> None:
        assert chapters_from_pages([]) == []
        assert chapters_from_pages([page([], number=1)]) == []

    def test_furniture_never_reaches_markdown(self) -> None:
        lines = [
            L("1.0 Capítulo", size=20, demi=True),
            L("Contenido real del capítulo."),
            L("Página 7 de 225"),
            L("Copyright © Todos los derechos reservados a Bantotal"),
            L("- MDI-10109-GL-V3R1.28-Sistema de Contrapartes, Jul.-26", y=760),
            L("Documento Privado"),
        ]
        md = chapters_from_pages([page(lines)])[0][1]
        assert "Página" not in md
        assert "Copyright" not in md
        assert "Jul.-26" not in md
        assert "Documento Privado" not in md
        assert "Contenido real" in md


# ─────────────────────────────────────────────────────────────────────────────
# Page spans — the provenance a citation is checked against
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestPageSpans:
    def test_single_page_chapter_spans_that_page(self) -> None:
        lines = [L("1.0 Capítulo", size=20, demi=True), L("Contenido del capítulo.")]
        (chapter,) = chapters_from_pages([page(lines, number=7)])
        assert (chapter.page_start, chapter.page_end) == (7, 7)

    def test_span_covers_every_page_the_chapter_draws_from(self) -> None:
        p2 = page([L("1.0 Capítulo", size=20, demi=True), L("Empieza aquí.")], number=2)
        p3 = page([L("Continúa en la página siguiente.")], number=3)
        p4 = page([L("Y termina aquí.")], number=4)
        (chapter,) = chapters_from_pages([p2, p3, p4])
        assert (chapter.page_start, chapter.page_end) == (2, 4)

    def test_each_chapter_gets_its_own_span(self) -> None:
        p2 = page([L("1.0 Primero", size=20, demi=True), L("Texto uno.")], number=2)
        p3 = page([L("2.0 Segundo", size=20, demi=True), L("Texto dos.")], number=3)
        first, second = chapters_from_pages([p2, p3])
        assert (first.page_start, first.page_end) == (2, 2)
        assert (second.page_start, second.page_end) == (3, 3)

    def test_rows_swallowed_by_a_table_region_still_count(self) -> None:
        # The table branch consumes rows in an inner loop, so a chapter whose
        # tail is all table must not report a span ending before the table.
        p2 = page(
            [
                L("1.0 Parámetros", size=20, demi=True, y=100),
                L("Nº", x=105, x1=125, y=140),
                L("Concepto", x=155, x1=215, y=140),
                L("Descripción", x=396, x1=460, y=140),
                L("307", x=100, x1=120, y=180),
                L("Correlativo país", x=133, x1=250, y=180),
                L("Valor por defecto.", x=312, x1=470, y=180),
            ],
            number=2,
        )
        p3 = page(
            [
                L("1335", x=99, x1=122, y=108),
                L("Motivos de baja", x=133, x1=250, y=100),
                L("Descripción del motivo.", x=312, x1=470, y=100),
            ],
            number=3,
        )
        (chapter,) = chapters_from_pages([p2, p3])
        assert "| 1335 |" in chapter.markdown
        assert (chapter.page_start, chapter.page_end) == (2, 3)

    def test_chapter_is_still_indexable_as_title_markdown(self) -> None:
        # Callers that only want the pair keep working: chapter[0], chapter[1].
        chapter = chapters_from_pages(
            [page([L("1.0 Capítulo", size=20, demi=True), L("Contenido.")])]
        )[0]
        assert chapter[0] == "1.0 Capítulo"
        assert "Contenido." in chapter[1]


# ─────────────────────────────────────────────────────────────────────────────
# Splitting chapters too wide to cite
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestWideChapterSplit:
    """A chapter over `_MAX_CITABLE_PAGES` (10) is cut on its own sub-headings.

    MDI-10201 needs it: only "N.0" lines open a chapter there, so its chapter 2
    ran 440 pages and every chunk cited "pp.126-565".
    """

    def _wide_doc(self, pages: int = 14) -> list[PageIR]:
        """Chapter 1.0 with a 1.1 on page 2 and a 1.2 halfway down."""
        out = [
            page([L("1.0 Parametrización", size=20, demi=True), L("Intro del capítulo.")], number=2),
            page([L("1.1 Comisiones", size=14, demi=True), L("Texto de comisiones.")], number=3),
        ]
        out += [page([L(f"Relleno de la página {n}.")], number=n) for n in range(4, pages)]
        out.append(page([L("1.2 Tasas", size=14, demi=True), L("Texto de tasas.")], number=pages))
        out.append(page([L("Cierre del capítulo.")], number=pages + 1))
        return out

    def test_a_narrow_chapter_is_not_split(self) -> None:
        # The prose has to outweigh the headings by character count, or 20pt
        # wins `_body_size` and the 14pt sub-heading reads as body text.
        pages = [
            page(
                [
                    L("1.0 Capítulo", size=20, demi=True),
                    L("Intro del capítulo con suficiente texto para fijar el cuerpo."),
                ],
                number=2,
            ),
            page(
                [
                    L("1.1 Sección", size=14, demi=True),
                    L("Texto de la sección, también largo para pesar en el promedio."),
                ],
                number=3,
            ),
        ]
        (chapter,) = chapters_from_pages(pages)
        assert chapter.section == ""
        assert "### 1.1 Sección" in chapter.markdown

    def test_a_wide_chapter_splits_on_its_subheadings(self) -> None:
        chapters = chapters_from_pages(self._wide_doc())
        assert len(chapters) > 1
        # Intro piece keeps no section; the rest are named by their sub-heading.
        assert [c.section for c in chapters] == ["", "1.1 Comisiones", "1.2 Tasas"]
        assert all(c.title == "1.0 Parametrización" for c in chapters)

    def test_split_pieces_carry_their_own_narrower_page_span(self) -> None:
        chapters = chapters_from_pages(self._wide_doc())
        spans = [(c.page_start, c.page_end) for c in chapters]
        # No piece may claim the whole chapter's range any more.
        assert max(e - s + 1 for s, e in spans) < 14
        # Spans move forward with the document and cover it without gaps.
        assert spans == sorted(spans)
        assert spans[0][0] == 2
        assert spans[-1][1] == 15

    def test_every_piece_keeps_the_chapter_h1_for_context(self) -> None:
        # A chunk read on its own must still say which chapter it belongs to.
        for chapter in chapters_from_pages(self._wide_doc()):
            assert chapter.markdown.startswith("# 1.0 Parametrización")

    def test_content_is_neither_lost_nor_duplicated_by_the_split(self) -> None:
        chapters = chapters_from_pages(self._wide_doc())
        joined = "\n".join(c.markdown for c in chapters)
        for needle in ("Intro del capítulo.", "Texto de comisiones.", "Texto de tasas.", "Cierre del capítulo."):
            assert joined.count(needle) == 1

    def test_a_wide_chapter_with_no_subheadings_stays_whole(self) -> None:
        # Nothing to cut on: the text is real, only the citation is coarse.
        pages = [page([L("1.0 Capítulo", size=20, demi=True), L("Intro.")], number=2)]
        pages += [page([L(f"Relleno {n}.")], number=n) for n in range(3, 18)]
        (chapter,) = chapters_from_pages(pages)
        assert (chapter.page_start, chapter.page_end) == (2, 17)
        assert chapter.section == ""
