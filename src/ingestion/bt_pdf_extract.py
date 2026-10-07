"""Extraction core: Bantotal PDF manuals -> agent-first Markdown chapters.

Feeds `local_loader._pdf_docs`, which turns each `Chapter` into a `RawDoc`. This
module owns *parsing only* — no filesystem writes, no ChromaDB, no metadata
contract; the manifest (`data/manuals.json`) still decides which manuals enter.

Design (grounded in first-hand inspection of the manuals declared in
`data/manuals.json`, rooted at `F:/ProyectosClaude/docs/Bantotal`):

- **IR-based**: the fitz-dependent layer (`extract_pages`) turns each page into
  a line IR; everything from IR to Markdown (`chapters_from_pages`) is pure and
  unit-tested without PDFs.
- **Furniture** is pattern-based (not recurrence-based): footers in this corpus
  are 3-4 varying lines per page ("Página N de M", "Copyright ©", the doc-code
  line, "Documento Privado"), so recurrence heuristics leak them into the
  corpus. The "Documento Privado" watermark shatters into 1-char runs along the
  bottom edge — those are filtered too.
- **TOC pages** are detected by dot-leader lines and skipped whole.
- **Headings** are detected by FONT (FranklinGothic-Demi/Medium), not size: in
  MDI-10110 body and headings share 8pt. Number and title may be separate lines
  ("1.1" / "Modos de Uso") — number-only headings consume the next Demi line,
  and chapter titles spanning several big Demi lines ("1.0 ..." + "(HME00029)")
  are merged. Level comes from the rank of distinct heading sizes (20pt -> H2,
  14pt -> H3, 11pt -> H4). Docs with no Demi headings (ETP) fall back to
  size-over-body.
- **Tables — region/anchor reconstruction.** `find_tables` was discarded: on
  this corpus double-drawn cell borders make PyMuPDF hallucinate 12-column
  grids, and text artifacts spawn nested phantom tables. Instead:
  1. A visual row with >= 2 header-vocab cells ("Nº", "Concepto", "Guía",
     "Correlativo", "Descripción", ...) opens a table region.
  2. Column x-starts are CLUSTERED from the region's own data lines — header
     positions are unreliable (headers are centered, data is left-aligned).
  3. Logical rows are anchored on the short cells of column 0 (the "Nº"
     values); row boundaries are the midpoints between consecutive anchors,
     which works because anchors are vertically centered in their rows
     (verified against the raw geometry).
  4. A region survives page breaks (furniture is already gone) and ends on
     headings, another header row, or a long prose line at the left margin.
  5. Lines before the first anchor ("Aplicación:" under "En la", "Programa"
     dropped 6pt below its row) extend the header labels.
- **Bullets** (Wingdings "▪" glyph cell + text cell) become `- ` list items.
- **Figure captions** ("Fig.N ...", italic) are kept as `*Fig.N ...*` — they
  are the only image information an agent gets (images are dropped).
- **Paragraphs** join hard-wrapped lines only when the next line starts
  lowercase and the previous does not end with `: ; =` — this protects field
  lists ("Axrubr = Código Contable del Préstamo") that must stay one-per-line.
- **Page spans**: each chapter records the first and last PDF page it drew
  content from (`Chapter.page_start` / `page_end`). Tables and paragraphs are
  reconstructed *across* page breaks, so a chapter has no single page — but the
  citation an agent emits still has to be checkable against the PDF, hence the
  range. Spans are derived from the stream-row indices at which each chapter
  opened, so rows swallowed whole by a table region are counted too.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

# ─────────────────────────────────────────────────────────────────────────────
# IR
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class LineIR:
    #: 1-based PDF page, so `page` always means the page a reader would cite.
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    font: str
    size: float
    is_demi: bool
    rotated: bool = False


@dataclass
class PageIR:
    #: 1-based, matching `LineIR.page`. Page 1 is the cover.
    number: int
    height: float
    lines: list[LineIR] = field(default_factory=list)
    #: Needed to tell a right-margin page reference from a left-hand table
    #: value; A4 by default so callers building IR by hand can skip it.
    width: float = 595.0


class Chapter(NamedTuple):
    """One extracted chapter, with the PDF page range it was drawn from.

    A NamedTuple rather than a dataclass so `chapter[0]` / `chapter[1]` still
    read as `(title, markdown)` — the pair is what most callers want, and the
    span is provenance they can ignore.
    """

    title: str
    markdown: str
    page_start: int  # 1-based, inclusive
    page_end: int  # 1-based, inclusive
    #: Sub-heading this piece was split at, when the chapter was too wide to
    #: cite as a whole ("2.3 Devengamiento"). Empty for an unsplit chapter.
    section: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Furniture (header/footer) — pattern-based
# ─────────────────────────────────────────────────────────────────────────────

#: Every document-code prefix Bantotal issues. Shared with `local_loader`, which
#: parses the same codes out of filenames for citation metadata: the two used to
#: keep private copies of this list and drifted twice. The second drift cost 260
#: footer lines of PRD-99000 leaking into the corpus as content, because the
#: furniture matcher had never heard of PRD. One list, one place.
DOC_CODE_PREFIXES = ("MDI", "MDU", "ETP", "ECP", "PRD", "CAP")
_DOC_CODE_ALT = "|".join(DOC_CODE_PREFIXES)

_FURNITURE_PATTERNS = [
    re.compile(r"^Página\s+\d+\s+de\s+\d+$", re.IGNORECASE),
    re.compile(r"^Copyright\s*©\s*Todos los derechos reservados a Bantotal\.?$", re.IGNORECASE),
    re.compile(r"^Documento\s+(Privado|Público|Confidencial)$", re.IGNORECASE),
]

# Doc-code footer line, e.g. "- MDI-10109-GL-V3R1.28-Sistema ..., Jul.-26".
# The localization segment is optional: ECP omits it entirely. Unlike the
# patterns above, a doc code is ALSO legitimate body text — "ver el documento
# MDU-10110-GL-V2R3" — so it only counts as furniture inside the footer band.
_DOC_CODE_RE = re.compile(rf"\b(?:{_DOC_CODE_ALT})-\d+(?:-[A-Z]{{2}})?-V", re.IGNORECASE)

# Measured across the corpus on 2026-10-07: the 3.337 doc-code footers start
# 50-88pt above the bottom edge; the 5 body cross-references, 267pt or more.
_FOOTER_BAND = 120.0


def is_furniture(line: LineIR, page_height: float) -> bool:
    """True for header/footer lines: pattern-matched, a bare page number near
    an edge (MDU-10205 prints it in 11pt body font), or a 1-2 char watermark
    fragment along the bottom edge ("Documento Privado" shatters there)."""
    t = line.text.strip()
    if not t:
        return True
    for pat in _FURNITURE_PATTERNS:
        if pat.search(t):
            return True
    if _DOC_CODE_RE.search(t) and line.y0 > page_height - _FOOTER_BAND:
        return True
    if re.fullmatch(r"\d{1,4}", t) and (line.y0 < 100 or line.y1 > page_height - 70):
        return True
    # Watermark fragments: the "Documento Privado" watermark shatters into
    # 1-2 char runs along the bottom edge — sometimes re-joined with spaces
    # into short strings like "t P i" or "D t P i d".
    if line.y1 > page_height - 70 and len(t) <= 12 and all(len(tok) <= 2 for tok in t.split()):
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# TOC pages
# ─────────────────────────────────────────────────────────────────────────────

_DOT_LEADER_RE = re.compile(r"\.{4,}\s*\d*\s*$")
_BARE_NUMBER_RE = re.compile(r"\d{1,4}")

# MDU-10115's index has no dot leaders at all — entry and page number are
# separate lines ("2.4.1.1.2 Modificar Pizarra de Tasas" / "39") — so it has to
# be recognised by the column of page references at the right margin.
#
# Position alone is not enough: MDI-10109 p.181 is real content with 15 bare
# numbers, some of them at 0.85, and dropping it lost a parametrisation table.
# What separates them is EXCLUSIVITY. On an index every bare number is a page
# reference, so they share one x (0.88 on both index pages measured); on a
# content page the numbers come in several columns (0.21, 0.39 AND 0.85).
_PAGE_REF_MIN_X_RATIO = 0.75
_PAGE_REF_MIN_SHARE = 0.8
_MIN_TOC_MARKERS = 3


def is_toc_page(lines: list[LineIR], page_width: float = 595.0) -> bool:
    """True for an index page: dot leaders, or right-margin page references.

    Indexed as content, an index is a keyword-dense list of every title in the
    manual — it matches almost any query on the topic and displaces the page
    that actually holds the answer.
    """
    leaders = sum(1 for ln in lines if _DOT_LEADER_RE.search(ln.text.strip()))
    if leaders >= _MIN_TOC_MARKERS:
        return True
    numbers = [ln for ln in lines if _BARE_NUMBER_RE.fullmatch(ln.text.strip())]
    right = [ln for ln in numbers if ln.x0 >= page_width * _PAGE_REF_MIN_X_RATIO]
    return (
        len(right) >= _MIN_TOC_MARKERS
        and len(right) >= _PAGE_REF_MIN_SHARE * len(numbers)
    )


def content_after_index(lines: list[LineIR], page_height: float) -> list[LineIR]:
    """The lines of an index page between its last dot-leader entry and the footer.

    Short documents open their first chapter right below the index; skipping
    the page whole lost section 1.0 of "Buenas Prácticas GX16" and the
    Objetivo of ETP-99000. Only the dot-leader style has a reliable end: an
    index of bare right-margin page numbers (MDU-10115) interleaves entries and
    numbers, so its page is still dropped whole — losing content there is less
    likely than leaking an index.

    The footer band is excluded because a footer block wraps onto lines with no
    doc code ("(PSNG9122)") that `is_furniture` cannot recognise, and on an
    index page they are the only thing below the last entry.
    """
    leaders = [ln.y0 for ln in lines if _DOT_LEADER_RE.search(ln.text.strip())]
    if not leaders:
        return []
    last = max(leaders)
    footer_top = page_height - _FOOTER_BAND
    return [ln for ln in lines if last < ln.y0 <= footer_top]


# ─────────────────────────────────────────────────────────────────────────────
# Headings
# ─────────────────────────────────────────────────────────────────────────────

_NUMBERED_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(\S.*)$")
_NUMBER_ONLY_RE = re.compile(r"^\d+(?:\.\d+)*\.?$")
_CHAPTER_RE = re.compile(r"^\d+\.0(?:\s|$)")

# Unnumbered chapter headings ("Introducción", 20pt) must beat the body by a
# wide margin; numbered Demi headings only need to be >= body size (MDU-10205
# prints deep headings like "1.2.3.1 Eliminar" in Demi at body size).
_CHAPTER_SIZE_MIN_DELTA = 8.0
_FALLBACK_SIZE_MIN_DELTA = 0.5


def _numbered_heading(line: LineIR, body_size: float) -> bool:
    return (
        line.is_demi
        and line.size >= body_size - 1e-6
        and bool(_NUMBERED_RE.match(line.text.strip()))
    )


def _is_number_only(line: LineIR, body_size: float) -> bool:
    return (
        line.is_demi
        and line.size >= body_size - 1e-6
        and bool(_NUMBER_ONLY_RE.fullmatch(line.text.strip()))
    )


def _unchaptered_heading(line: LineIR, body_size: float) -> bool:
    """Unnumbered big Demi headings (e.g. 'Introducción') are chapter-level."""
    return (
        line.is_demi
        and line.size >= body_size + _CHAPTER_SIZE_MIN_DELTA
        and len(line.text.strip()) >= 4
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tables — region/anchor reconstruction
# ─────────────────────────────────────────────────────────────────────────────

_HEADER_VOCAB = {
    "Nº", "N°", "Concepto", "Descripción", "Descripcion", "Programa",
    "Guía", "Guia", "Correlativo", "Valor", "Campo", "Código", "Codigo",
    "Tabla", "Empresa", "Aplicación:", "Aplicación", "Tipo", "Nombre",
}

# Column x-starts cluster within this tolerance (data is left-aligned per
# column; wrapped cell lines share the column's x0 almost exactly).
_COL_CLUSTER_TOL = 18.0
# A column-0 cell this short is a row anchor (Nº values, "Empresa", "Ayer").
_ANCHOR_MAX_LEN = 30


def _vocab_score(cells: list[str]) -> int:
    score = 0
    for cell in cells:
        tokens = set(re.split(r"\s+", cell.strip()))
        if tokens & _HEADER_VOCAB:
            score += 1
    return score


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|")


def _merge_to(clusters: list[float], target: int) -> list[float]:
    """Fold the closest-together bands into their left neighbour until `target`.

    Clustering by x0 over-segments when a column's cells are CENTERED rather
    than left-aligned: the start then moves with the text width. MDI-10109's
    data-structure tables do that — one Descripción column produced starts from
    234 to 274 and split into three bands. The header row knows how many columns
    there really are, so its count caps the result and the widest gaps survive.
    Only ever merges; a header that under-counts cannot delete a real column.
    """
    clusters = list(clusters)
    while len(clusters) > target:
        _, drop = min(
            (clusters[i + 1] - clusters[i], i + 1) for i in range(len(clusters) - 1)
        )
        clusters.pop(drop)
    return clusters


def _cluster_columns(lines: list[LineIR], max_columns: int | None = None) -> list[float]:
    """Column x-starts from the region's data lines, clustered by proximity."""
    xs = sorted(round(ln.x0, 1) for ln in lines)
    if not xs:
        return []
    clusters = [xs[0]]
    for x in xs[1:]:
        if x - clusters[-1] <= _COL_CLUSTER_TOL:
            continue  # same column (keep the leftmost start)
        clusters.append(x)
    if max_columns and 2 <= max_columns < len(clusters):
        clusters = _merge_to(clusters, max_columns)
    return clusters


def _nearest_col(x0: float, col_xs: list[float]) -> int | None:
    for i, cx in enumerate(col_xs):
        upper = col_xs[i + 1] - _COL_CLUSTER_TOL if i + 1 < len(col_xs) else float("inf")
        if cx - _COL_CLUSTER_TOL <= x0 < upper:
            return i
    return None


@dataclass
class _RegionTable:
    header: list[str]
    rows: list[list[str]]

    def emit(self) -> list[str]:
        if not self.rows:
            return []
        header = self.header or [f"Col {i + 1}" for i in range(len(self.rows[0]))]
        out = [
            "| " + " | ".join(_escape_cell(h) for h in header) + " |",
            "| " + " | ".join("---" for _ in header) + " |",
        ]
        out.extend("| " + " | ".join(_escape_cell(c) for c in r) + " |" for r in self.rows)
        return out


def _build_table_from_region(
    header_cells: list[LineIR],
    region_lines: list[LineIR],
) -> _RegionTable | None:
    """Rebuild a borderless/ruled table from raw region lines.

    Column x-starts come from clustering the region lines themselves (headers
    are centered, data is left-aligned — header positions are NOT the bands).
    Logical rows anchor on short column-0 cells; boundaries are the midpoints
    between consecutive anchor CENTERS (anchors are vertically centered in
    their rows, so a row's first cell lines legitimately sit above it).
    """
    col_xs = _cluster_columns(region_lines, max_columns=len(header_cells))
    if len(col_xs) < 2:
        return None

    # Some tables have Nº and Concepto glued into a single line because their
    # adjacent spans were joined ("23570 Hab. integración"). When such a line
    # sits in column 0 and carries > 1 token, split it: first token to col 0,
    # remainder to col 1 at the column-1 x-start.
    split_lines: list[LineIR] = []
    for ln in region_lines:
        if abs(ln.x0 - col_xs[0]) > _COL_CLUSTER_TOL:
            split_lines.append(ln)
            continue
        tokens = ln.text.strip().split(maxsplit=1)
        if len(tokens) < 2 or len(tokens[0]) > _ANCHOR_MAX_LEN:
            split_lines.append(ln)
            continue
        anchor_token, rest = tokens
        split_lines.append(LineIR(ln.page, ln.x0, ln.y0, ln.x1, ln.y1, anchor_token, ln.font, ln.size, ln.is_demi, ln.rotated))
        rest_x0 = col_xs[1] if len(col_xs) > 1 else ln.x1
        split_lines.append(LineIR(ln.page, rest_x0, ln.y0, max(ln.x1, rest_x0 + 50), ln.y1, rest, ln.font, ln.size, ln.is_demi, ln.rotated))
    region_lines = split_lines

    anchors = [
        ln for ln in region_lines
        if abs(ln.x0 - col_xs[0]) <= _COL_CLUSTER_TOL and 0 < len(ln.text.strip()) <= _ANCHOR_MAX_LEN
    ]
    # One anchor per y (merged cells can duplicate).
    seen_y: set[int] = set()
    uniq_anchors: list[LineIR] = []
    for ln in sorted(anchors, key=lambda a: a.y0):
        key = round(ln.y0)
        if key not in seen_y:
            seen_y.add(key)
            uniq_anchors.append(ln)
    if not uniq_anchors:
        return None

    # Anchor CENTERS as the row pivot: a line's nearest anchor is its row
    # (lines are roughly centered around their anchor in this corpus). Ties
    # break to the LATER row — the geometry is not perfectly symmetric (desc
    # cells tend to be taller than concept cells), so late lines prefer the
    # next row, which keeps concept cells with their own anchor.
    anchor_cs = [(a.y0 + a.y1) / 2 for a in uniq_anchors]
    # With page-offset normalization, anchors stay sorted even across pages
    # (the next page's first anchor is far below the previous page's last).
    anchor_by_y = {round(a.y0): idx for idx, a in enumerate(uniq_anchors)}

    rows: list[list[str]] = [[""] * len(col_xs) for _ in uniq_anchors]
    for ln in region_lines:
        col = _nearest_col(ln.x0, col_xs)
        if col is None:
            continue
        idx = anchor_by_y.get(round(ln.y0))
        if idx is not None and col == 0:
            row_idx = idx
        else:
            yc = (ln.y0 + ln.y1) / 2
            # Nearest anchor by center distance; ties go to the LATER row
            # (desc cells are usually taller, so late lines prefer the next).
            row_idx = min(range(len(anchor_cs)), key=lambda i: (abs(yc - anchor_cs[i]), -i))
        text = ln.text.strip()
        rows[row_idx][col] = f"{rows[row_idx][col]} {text}".strip() if rows[row_idx][col] else text

    header = [""] * len(col_xs)
    if len(header_cells) == len(col_xs):
        # Same count: labels and columns are both left-to-right sequences, so
        # ORDER beats x-proximity here. Headers are centred over their column
        # while the data that defined the bands is left-aligned, so a nearest-x
        # match lands labels one column off — MDI-10109's agenda table came out
        # as "Nº | Col 2 | Concepto Programa | Descripción".
        for idx, cell in enumerate(sorted(header_cells, key=lambda c: c.x0)):
            header[idx] = cell.text.strip()
    else:
        # Counts disagree (a wrapped label was absorbed, or a column has no
        # header at all): fall back to nearest-column matching.
        for cell in header_cells:
            col = _nearest_col(cell.x0, col_xs)
            if col is not None:
                header[col] = (
                    f"{header[col]} {cell.text.strip()}".strip()
                    if header[col]
                    else cell.text.strip()
                )
    header = [h or f"Col {i + 1}" for i, h in enumerate(header)]

    rows = [r for r in rows if any(r)]
    return _RegionTable(header=header, rows=rows)


# ─────────────────────────────────────────────────────────────────────────────
# fitz layer: PDF -> IR (the only PyMuPDF-dependent code)
# ─────────────────────────────────────────────────────────────────────────────


def _join_spans(spans: list) -> str:
    """Concatenate span texts, inserting a space when the horizontal gap between
    consecutive spans shows they were separate words ("1.0" + "Mantenimiento"
    would otherwise glue into "1.0Mantenimiento")."""
    parts: list[str] = []
    prev_x1: float | None = None
    for s in spans:
        text = s["text"]
        if (
            parts
            and prev_x1 is not None
            and s["bbox"][0] - prev_x1 > 1.0
            and not parts[-1].endswith((" ", "-"))
            and not text.startswith(" ")
        ):
            parts.append(" ")
        parts.append(text)
        prev_x1 = s["bbox"][2]
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _lines_from_page(page, page_no: int) -> list[LineIR]:
    out: list[LineIR] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            rotated = line.get("dir", (1.0, 0.0)) != (1.0, 0.0)
            spans = [s for s in line.get("spans", []) if s["text"].strip()]
            if not spans:
                continue
            text = _join_spans(spans)
            if not text:
                continue
            by_chars: Counter[tuple[str, float]] = Counter()
            for s in spans:
                by_chars[(s["font"], round(s["size"], 1))] += len(s["text"])
            (font, size), _ = by_chars.most_common(1)[0]
            is_demi = all(("Demi" in s["font"] or "Medium" in s["font"]) for s in spans)
            x0, y0, x1, y1 = line["bbox"]
            out.append(LineIR(page_no, x0, y0, x1, y1, text, font, size, is_demi, rotated))
    return out


def extract_pages(pdf_path: str | Path) -> list[PageIR]:
    """PDF -> per-page line IR."""
    import fitz  # pymupdf — imported lazily so the pure layer stays testable

    doc = fitz.open(str(pdf_path))
    pages: list[PageIR] = []
    for pno in range(len(doc)):
        page = doc[pno]
        number = pno + 1  # 1-based: the page number a citation must survive as
        pages.append(
            PageIR(
                number=number,
                height=float(page.rect.height),
                lines=_lines_from_page(page, number),
                width=float(page.rect.width),
            )
        )
    doc.close()
    return pages


# ─────────────────────────────────────────────────────────────────────────────
# Pure layer: IR -> Markdown chapters
# ─────────────────────────────────────────────────────────────────────────────

_BULLET_GLYPH_RE = re.compile(r"^[▪•]\s*(.*)$")
_FIG_CAPTION_RE = re.compile(r"^Fig\.?\s*\d")
_NO_JOIN_END_RE = re.compile(r"[:;=]$")
# Header cells dropped a few pt below their row-mates ("Programa" at y+6) must
# still land in the same visual row; wrapped cell lines (~12pt apart) must not.
_VISUAL_ROW_Y_TOL = 8.0


def _body_size(lines: list[LineIR]) -> float:
    by_chars: Counter[float] = Counter()
    for ln in lines:
        by_chars[ln.size] += len(ln.text)
    return by_chars.most_common(1)[0][0] if by_chars else 11.0


@dataclass
class _VisualRow:
    y0: float
    cells: list[LineIR]  # sorted by x0

    @property
    def single(self) -> LineIR | None:
        return self.cells[0] if len(self.cells) == 1 else None


def _visual_rows(lines: list[LineIR]) -> list[_VisualRow]:
    """Cluster dict-lines into visual rows by y-proximity. Table cells live on
    separate dict-lines at the same y; prose lines stay single-cell rows."""
    rows: list[_VisualRow] = []
    for ln in sorted(lines, key=lambda ln: (ln.y0, ln.x0)):
        if rows and abs(ln.y0 - rows[-1].y0) <= _VISUAL_ROW_Y_TOL:
            rows[-1].cells.append(ln)
        else:
            rows.append(_VisualRow(y0=ln.y0, cells=[ln]))
    for row in rows:
        row.cells.sort(key=lambda ln: ln.x0)
    return rows


def _is_header_row(row: _VisualRow) -> bool:
    """A visual row of >= 2 short cells opens a table region when its FIRST
    cell is a header label and >= 2 cells hit the vocab (e.g. 'Nº | Concepto |
    En la | Descripción'). The first-cell requirement kills false positives
    like data row '179 | Habilita Nombre reducido | Valor N'."""
    if len(row.cells) < 2:
        return False
    texts = [c.text.strip() for c in row.cells]
    if any(len(t) > 40 for t in texts):
        return False
    first_tokens = set(re.split(r"\s+", texts[0]))
    if not (first_tokens & _HEADER_VOCAB):
        return False
    return _vocab_score(texts) >= 2


# A chapter spanning more pages than this cannot be cited usefully — "pp.126-565"
# tells a reader nothing — so it is split on its own sub-headings. MDI-10201
# forces the issue: only "N.0" lines open a chapter there, and its chapter 2 runs
# 440 pages. Pieces with no deeper heading left stay wide; the text is real, only
# the citation is coarse.
_MAX_CITABLE_PAGES = 10

#: A sub-heading inside a chapter: (block index, level, stream row, text). The
#: text is carried rather than re-parsed out of the rendered "### …" block —
#: it is known when the mark is made, and a parser would break silently if the
#: heading format ever changed.
_Mark = tuple[int, int, int, str]


@dataclass
class _RawChapter:
    """A chapter before the citability check: blocks plus where they came from."""

    title: str
    blocks: list[str]
    marks: list[_Mark]
    row_start: int
    row_end: int
    section: str = ""


def _page_span(stream: list[_VisualRow], start: int, end: int) -> tuple[int, int]:
    pages = [c.page for row in stream[start:end] for c in row.cells]
    return min(pages, default=0), max(pages, default=0)


def _assemble(title: str, blocks: list[str]) -> str:
    return "\n\n".join(([f"# {title}"] if title else []) + blocks).strip()


def _split_chapter(raw: _RawChapter, stream: list[_VisualRow]) -> list[Chapter]:
    """One raw chapter -> one or more citable pieces.

    Splits on the SHALLOWEST sub-heading level present, then recurses: a piece
    still too wide after cutting at level 3 is cut again at its level-4
    headings. Levels strictly increase, so the recursion terminates. Every piece
    keeps the chapter's H1, so a chunk read on its own still says where it is.
    """
    page_start, page_end = _page_span(stream, raw.row_start, raw.row_end)
    md = _assemble(raw.title, raw.blocks)
    if not md:
        return []
    if page_end - page_start + 1 <= _MAX_CITABLE_PAGES or not raw.marks:
        return [Chapter(raw.title, md, page_start, page_end, raw.section)]

    level = min(lvl for _, lvl, _, _ in raw.marks)
    # Edges: the pre-heading intro (inheriting the parent's section), then one
    # per sub-heading at `level`.
    edges = [(0, raw.row_start, raw.section)]
    edges += [
        (block_idx, row, text)
        for block_idx, lvl, row, text in raw.marks
        if lvl == level
    ]

    out: list[Chapter] = []
    for k, (block_idx, row, section) in enumerate(edges):
        next_idx = edges[k + 1][0] if k + 1 < len(edges) else len(raw.blocks)
        next_row = edges[k + 1][1] if k + 1 < len(edges) else raw.row_end
        blocks = raw.blocks[block_idx:next_idx]
        if not blocks:
            continue
        inner = [
            (b - block_idx, lvl, r, text)
            for b, lvl, r, text in raw.marks
            if block_idx <= b < next_idx and lvl > level
        ]
        out.extend(
            _split_chapter(
                _RawChapter(raw.title, blocks, inner, row, next_row, section), stream
            )
        )
    return out


def chapters_from_pages(pages: list[PageIR]) -> list[Chapter]:
    """IR -> chapters. The only entry the caller needs."""
    # 1. Preprocess: drop cover, rotated lines, furniture, TOC pages.
    usable: list[PageIR] = []
    for p in pages:
        if p.number <= 1:
            continue  # cover (page 1): the title comes from the filename
        lines = [ln for ln in p.lines if not ln.rotated and not is_furniture(ln, p.height)]
        if is_toc_page(lines, p.width):
            lines = content_after_index(lines, p.height)
            if not lines:
                continue
        usable.append(
            PageIR(number=p.number, height=p.height, lines=lines, width=p.width)
        )
    all_lines = [ln for p in usable for ln in p.lines]
    if not all_lines:
        return []

    body = _body_size(all_lines)

    # 2. Heading-level map from distinct heading sizes (desc): biggest -> H2.
    has_demi_headings = any(
        _numbered_heading(ln, body) or _is_number_only(ln, body) for ln in all_lines
    )
    heading_sizes = sorted(
        {
            ln.size
            for ln in all_lines
            if _numbered_heading(ln, body) or _is_number_only(ln, body) or _unchaptered_heading(ln, body)
        },
        reverse=True,
    )

    def heading_level(line: LineIR) -> int | None:
        if has_demi_headings:
            if not (
                _numbered_heading(line, body)
                or _is_number_only(line, body)
                or _unchaptered_heading(line, body)
            ):
                return None
        else:
            # ETP-style docs: no Demi structure — fall back to size over body.
            if line.size < body + _FALLBACK_SIZE_MIN_DELTA or len(line.text) > 80:
                return None
        try:
            return min(2 + heading_sizes.index(line.size), 6)
        except ValueError:
            return 2 if not has_demi_headings else None

    # 3. Global stream of visual rows (tables span pages: furniture and TOC
    #    pages are already gone, so rows flow across page boundaries). Y is
    #    normalized to a document-global axis so anchor bounds stay ordered.
    stream: list[_VisualRow] = []
    offset = 0.0
    for p in usable:
        for ln in p.lines:
            ln.y0 += offset
            ln.y1 += offset
        stream.extend(_visual_rows(p.lines))
        offset += p.height

    chapters: list[tuple[str, list[str]]] = [("", [])]
    # Stream index at which each chapter opened, parallel to `chapters`. The
    # rows in [bounds[k], bounds[k+1]) are chapter k's, and their LineIR.page
    # values give its page span — including rows a table region swallowed whole.
    bounds: list[int] = [0]
    # Sub-headings inside each chapter, parallel to `chapters`. A chapter too
    # wide to cite is split on these — see `_split_chapter`.
    marks: list[list[_Mark]] = [[]]
    para: list[str] = []
    pending_bullet = False
    pending_number_heading: LineIR | None = None
    expect_title_cont = False

    def flush_para() -> None:
        nonlocal para, expect_title_cont
        if para:
            chapters[-1][1].append(" ".join(para))
            para = []
            expect_title_cont = False

    def start_chapter(title: str) -> None:
        nonlocal expect_title_cont
        flush_para()
        # `i` already points past the heading row, so the chapter starts at i-1.
        if chapters[-1][1] or chapters[-1][0]:
            chapters.append((title, []))
            bounds.append(i - 1)
            marks.append([])
        else:
            chapters[-1] = (title, [])
            bounds[-1] = i - 1
            marks[-1] = []
        # Multi-line chapter titles ("1.0 ..." / "(HME00029)" on the next line)
        # extend the title until real content arrives.
        expect_title_cont = True

    def emit_heading(level: int, text: str) -> None:
        nonlocal expect_title_cont
        flush_para()
        expect_title_cont = False
        # Marked before the append so the index points at the heading block.
        marks[-1].append((len(chapters[-1][1]), level, i - 1, text))
        chapters[-1][1].append(f"{'#' * level} {text}")

    def is_terminator(row: _VisualRow) -> bool:
        """Rows that close a table region while scanning ahead."""
        if _is_header_row(row):
            return True
        single = row.single
        if single is None:
            return False
        if heading_level(single) is not None or _is_number_only(single, body):
            return True
        # Long prose at the left margin is not table content.
        return len(single.text.strip()) >= 60 and single.x0 <= 110

    i = 0
    while i < len(stream):
        row = stream[i]
        i += 1

        # ── Table region ────────────────────────────────────────────────
        if _is_header_row(row):
            flush_para()
            header_cells = list(row.cells)
            # Wrapped header labels ("Aplicación:" under "En la") ride as
            # single-cell rows right after the header. Absorb them — but never
            # anchor-like data (numeric, or repeating a header label verbatim,
            # e.g. ETP's "Empresa" column values).
            header_texts = {c.text.strip() for c in header_cells}
            while i < len(stream):
                nxt = stream[i]
                cand = nxt.single
                if cand is None:
                    break
                t = cand.text.strip()
                if (
                    len(t) >= 30
                    or cand.is_demi
                    or re.fullmatch(r"\d[\d.,]*", t)
                    or t in header_texts
                    or not any(abs(cand.x0 - c.x0) <= _COL_CLUSTER_TOL for c in header_cells)
                ):
                    break
                header_cells.append(cand)
                header_texts.add(t)
                i += 1
            region: list[LineIR] = []
            while i < len(stream) and not is_terminator(stream[i]):
                region.extend(stream[i].cells)
                i += 1
            table = _build_table_from_region(header_cells, region)
            if table:
                # ONE block, not one per row: blocks are joined with a blank
                # line, and a blank line between rows stops it being a Markdown
                # table at all — it renders as loose paragraphs of pipes.
                chapters[-1][1].append("\n".join(table.emit()))
            else:
                # False positive: header row + region are prose after all.
                para.append(" ".join(c.text.strip() for c in row.cells))
                for ln in region:
                    para.append(ln.text)
                flush_para()
            expect_title_cont = False
            continue

        single = row.single

        # ── Number-only heading: consume, wait for the title line ───────
        if single is not None and _is_number_only(single, body) and has_demi_headings:
            expect_title_cont = False
            flush_para()
            pending_number_heading = single
            continue

        if pending_number_heading is not None:
            head = pending_number_heading
            pending_number_heading = None
            text = head.text.strip()
            if single is not None and single.is_demi and single.size >= body - 1e-6:
                text = f"{text} {single.text.strip()}"
                level = heading_level(head) or 2
                if _CHAPTER_RE.match(text):
                    start_chapter(text)
                else:
                    emit_heading(level, text)
                continue
            level = heading_level(head) or 2
            emit_heading(level, text)
            # fall through to process the current row normally

        # ── Bullet row: glyph cell + text cell(s) at the same y ─────────
        if row.cells and _BULLET_GLYPH_RE.match(row.cells[0].text.strip()) and not _BULLET_GLYPH_RE.match(row.cells[0].text.strip()).group(1):
            expect_title_cont = False
            flush_para()
            item = " ".join(c.text.strip() for c in row.cells[1:]).strip()
            if item:
                chapters[-1][1].append(f"- {item}")
            else:
                pending_bullet = True
            continue

        if single is None:
            expect_title_cont = False
            flush_para()
            para.append(" ".join(c.text.strip() for c in row.cells))
            continue

        line = single
        text = line.text.strip()

        # ── Chapter-title continuation (big Demi line right after split) ─
        if expect_title_cont:
            if (
                line.is_demi
                and line.size >= body + _CHAPTER_SIZE_MIN_DELTA
                and not _NUMBERED_RE.match(text)
                and not _is_number_only(line, body)
            ):
                chapters[-1] = (f"{chapters[-1][0]} {text}".strip(), chapters[-1][1])
                continue
            expect_title_cont = False

        # ── Headings ────────────────────────────────────────────────────
        level = heading_level(line)
        if level is not None:
            if _CHAPTER_RE.match(text) or (_unchaptered_heading(line, body) and has_demi_headings):
                start_chapter(text)
            else:
                emit_heading(level, text)
            continue

        # ── Bullets (glyph + text on one dict-line) ─────────────────────
        m = _BULLET_GLYPH_RE.match(text)
        if m:
            expect_title_cont = False
            flush_para()
            rest = m.group(1).strip()
            if rest:
                chapters[-1][1].append(f"- {rest}")
            else:
                pending_bullet = True
            continue
        if pending_bullet:
            chapters[-1][1].append(f"- {text}")
            pending_bullet = False
            continue
        if text.startswith("- "):
            flush_para()
            chapters[-1][1].append(text)
            continue

        # ── Figure captions stay as italic placeholders (images dropped) ─
        if _FIG_CAPTION_RE.match(text):
            flush_para()
            chapters[-1][1].append(f"*{text}*")
            continue

        # ── Prose: join hard-wrapped continuations only (protect fields) ─
        if para and text[0].islower() and not _NO_JOIN_END_RE.search(para[-1].rstrip()):
            para.append(text)
        else:
            flush_para()
            para.append(text)

    flush_para()

    # 4. Assemble: H1 from the chapter title, then the blocks. Each chapter's
    #    page span comes from the stream rows it owns; chapters too wide to cite
    #    are split on their own sub-headings.
    bounds.append(len(stream))
    out: list[Chapter] = []
    for idx, (title, blocks) in enumerate(chapters):
        raw = _RawChapter(title, blocks, marks[idx], bounds[idx], bounds[idx + 1])
        out.extend(_split_chapter(raw, stream))
    return out


def extract_chapters(pdf_path: str | Path) -> list[Chapter]:
    """PDF path -> chapters with page spans. Empty list when nothing usable."""
    return chapters_from_pages(extract_pages(pdf_path))
