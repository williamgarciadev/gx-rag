"""PDF ingestion pipeline for Bantotal technical and functional manuals.

Extracts high-density semantic Markdown from Bantotal manuals:
1. Page layout analysis and artifact filtering (stripping recurring headers, footers, page numbers).
2. Table and parameter grid reconstruction to valid Markdown tables (| Col | Tipo |).
3. Automatic detection of Bantotal programs (B701, RTE...) and tables (FSD011, FST900...).
4. Modular multi-engine support: PyMuPDF table finder for digital PDFs + PaddleOCR / PP-Structure.
5. Self-contained Markdown generation with YAML frontmatter optimized for RAG retrieval.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Bantotal transaction & table patterns (e.g., B701, FSD011, FST900, RTE99, PA01)
_BT_TABLE_PATTERN = re.compile(r"\b(FS[A-Z0-9]{3,6})\b", re.IGNORECASE)
_BT_PROGRAM_PATTERN = re.compile(r"\b([A-Z]{1,4}\d{2,5}[A-Z0-9]?)\b")


@dataclass
class PDFTable:
    """A structured table extracted from a PDF page."""

    page_number: int
    headers: list[str]
    rows: list[list[str]]

    def to_markdown(self) -> str:
        """Convert tabular data into clean GitHub-flavored Markdown table."""
        if not self.headers and not self.rows:
            return ""

        headers = [h.strip().replace("\n", " ") for h in (self.headers or self.rows[0])]
        content_rows = self.rows if self.headers else self.rows[1:]

        # Escape pipe characters inside cells
        clean_headers = [h.replace("|", "\\|") if h else " " for h in headers]
        col_count = len(clean_headers)

        lines = [
            "| " + " | ".join(clean_headers) + " |",
            "| " + " | ".join(["---"] * col_count) + " |",
        ]

        for row in content_rows:
            padded_row = list(row) + [""] * (col_count - len(row))
            clean_cells = [
                str(cell).strip().replace("\n", " ").replace("|", "\\|")
                for cell in padded_row[:col_count]
            ]
            lines.append("| " + " | ".join(clean_cells) + " |")

        return "\n".join(lines)


@dataclass
class PDFPageContent:
    """Clean extracted content of a single PDF page."""

    page_number: int
    text_blocks: list[str] = field(default_factory=list)
    tables: list[PDFTable] = field(default_factory=list)
    programs: list[str] = field(default_factory=list)
    tables_detected: list[str] = field(default_factory=list)


@dataclass
class PDFDocumentMetadata:
    """Document-level metadata for manual ingestion."""

    source_file: str
    title: str
    module: str
    total_pages: int
    programs: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


def extract_bt_identifiers(text: str) -> tuple[list[str], list[str]]:
    """Extract Bantotal programs and tables from text."""
    raw_tables = sorted(set(t.upper() for t in _BT_TABLE_PATTERN.findall(text)))
    raw_progs = set(_BT_PROGRAM_PATTERN.findall(text))
    # Exclude identifiers that are already identified as tables
    programs = sorted(
        p for p in raw_progs if p.upper() not in raw_tables and not p.upper().startswith("FS")
    )
    return programs, raw_tables


class PDFLayoutExtractor:
    """Extracts text and tables while filtering recurring header/footer artifacts."""

    def __init__(
        self,
        header_margin_pct: float = 0.08,
        footer_margin_pct: float = 0.08,
    ):
        self.header_margin_pct = header_margin_pct
        self.footer_margin_pct = footer_margin_pct

    def extract_document(self, pdf_path: Path) -> list[PDFPageContent]:
        """Extract pages from PDF using PyMuPDF layout analysis."""
        import pymupdf

        doc = pymupdf.open(str(pdf_path))
        pages_content: list[PDFPageContent] = []

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            page_num = page_idx + 1
            rect = page.rect
            page_height = rect.height

            header_limit = page_height * self.header_margin_pct
            footer_limit = page_height * (1.0 - self.footer_margin_pct)

            # 1. Extract tables via PyMuPDF native table finder
            tables: list[PDFTable] = []
            table_bboxes = []
            try:
                tabs = page.find_tables()
                for tab in tabs:
                    table_bboxes.append(tab.bbox)
                    extracted = tab.extract()
                    if extracted and len(extracted) > 1:
                        headers = [str(c or "") for c in extracted[0]]
                        rows = [[str(c or "") for c in r] for r in extracted[1:]]
                        tables.append(
                            PDFTable(page_number=page_num, headers=headers, rows=rows)
                        )
            except Exception as e:
                log.debug(f"Table extraction skipped on page {page_num}: {e}")

            # 2. Extract text blocks outside header/footer zones and table bounding boxes
            text_blocks: list[str] = []
            blocks = page.get_text("blocks")  # (x0, y0, x1, y1, text, block_no, block_type)

            for b in blocks:
                if len(b) < 5 or b[6] != 0:  # block_type 0 is text
                    continue
                y0, y1, text = b[1], b[3], b[4].strip()
                if not text:
                    continue

                # Filter running header and footer areas
                if y0 < header_limit or y1 > footer_limit:
                    # Ignore pure page numbers or repetitive manual title headers
                    if text.isdigit() or len(text) < 40:
                        continue

                # Skip blocks that overlap with already extracted tables
                overlaps_table = False
                for tbox in table_bboxes:
                    if not (b[2] < tbox[0] or b[0] > tbox[2] or b[3] < tbox[1] or b[1] > tbox[3]):
                        overlaps_table = True
                        break

                if not overlaps_table:
                    # Normalize line wraps
                    clean_text = re.sub(r"\s*\n\s*", " ", text).strip()
                    if clean_text:
                        text_blocks.append(clean_text)

            combined_page_text = " ".join(text_blocks)
            progs, tbls = extract_bt_identifiers(combined_page_text)

            pages_content.append(
                PDFPageContent(
                    page_number=page_num,
                    text_blocks=text_blocks,
                    tables=tables,
                    programs=progs,
                    tables_detected=tbls,
                )
            )

        return pages_content


class PDFSynthesizer:
    """Generates high-density, self-contained Markdown from extracted pages."""

    @classmethod
    def generate_markdown(
        cls,
        meta: PDFDocumentMetadata,
        pages: list[PDFPageContent],
    ) -> str:
        """Construct structured Markdown with YAML frontmatter ready for RAG retrieval."""
        all_programs = sorted(
            set(meta.programs + [p for page in pages for p in page.programs])
        )
        all_tables = sorted(
            set(meta.tables + [t for page in pages for t in page.tables_detected])
        )

        frontmatter = {
            "source_type": "manual_pdf",
            "source_file": meta.source_file,
            "title": meta.title,
            "module": meta.module,
            "total_pages": meta.total_pages,
            "programs_involved": all_programs,
            "tables_touched": all_tables,
            "tags": list(set(["bantotal", "manual", meta.module.lower()] + meta.tags)),
        }

        yaml_lines = ["---"]
        for key, val in frontmatter.items():
            if isinstance(val, list):
                if not val:
                    yaml_lines.append(f"{key}: []")
                else:
                    yaml_lines.append(f"{key}:")
                    for item in val:
                        yaml_lines.append(f"  - \"{item}\"")
            else:
                yaml_lines.append(f"{key}: \"{val}\"")
        yaml_lines.append("---")
        yaml_header = "\n".join(yaml_lines)

        md_parts = [
            yaml_header,
            "",
            f"# {meta.title}",
            "",
            "## Resumen Ejecutivo de Negocio",
            "",
            f"**Módulo:** {meta.module}  ",
            f"**Archivo Fuente:** `{meta.source_file}`  ",
            f"**Páginas Totales:** {meta.total_pages}  ",
            f"**Programas / Transacciones:** {', '.join(f'`{p}`' for p in all_programs) if all_programs else 'N/A'}  ",
            f"**Tablas Bantotal:** {', '.join(f'`{t}`' for t in all_tables) if all_tables else 'N/A'}",
            "",
            f"Manual técnico y funcional de Bantotal correspondiente al módulo **{meta.module}**. "
            f"Define estructuras de datos, pantallas de parametrización y lógica de negocio asociada.",
            "",
            "---",
            "",
            "## Contenido y Especificaciones Técnicas",
            "",
        ]

        for page in pages:
            # Skip empty pages
            if not page.text_blocks and not page.tables:
                continue

            page_header = f"### [Página {page.page_number}]"
            if page.programs:
                page_header += f" - Transacción {', '.join(f'`{p}`' for p in page.programs)}"
            md_parts.append(page_header)
            md_parts.append("")

            # Render text blocks
            for block in page.text_blocks:
                md_parts.append(block)
                md_parts.append("")

            # Render tables
            for table in page.tables:
                table_md = table.to_markdown()
                if table_md:
                    md_parts.append("**Tabla de Parámetros / Estructura:**")
                    md_parts.append(table_md)
                    md_parts.append("")

            md_parts.append("")

        return "\n".join(md_parts).strip() + "\n"


class PDFIngestPipeline:
    """Coordinates PDF parsing, filtering, and Markdown persistence."""

    def __init__(self, extractor: PDFLayoutExtractor | None = None):
        self.extractor = extractor or PDFLayoutExtractor()

    def process(
        self,
        pdf_path: Path,
        output_dir: Path,
        title: str | None = None,
        module: str = "Bantotal General",
    ) -> tuple[Path, Path, str]:
        """Process a PDF manual into structured Markdown and metadata JSON.

        Returns (md_path, meta_path, markdown_content).
        """
        pdf_path = Path(pdf_path).resolve()
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        stem = pdf_path.stem
        output_dir.mkdir(parents=True, exist_ok=True)

        doc_title = title or f"Manual Bantotal — {stem.replace('_', ' ').replace('-', ' ').title()}"

        pages = self.extractor.extract_document(pdf_path)

        meta = PDFDocumentMetadata(
            source_file=pdf_path.name,
            title=doc_title,
            module=module,
            total_pages=len(pages),
        )

        markdown = PDFSynthesizer.generate_markdown(meta, pages)

        md_path = output_dir / f"{stem}.md"
        meta_path = output_dir / f"{stem}.meta.json"

        md_path.write_text(markdown, encoding="utf-8")

        meta_dict = asdict(meta)
        meta_dict["md_file"] = md_path.name
        meta_path.write_text(json.dumps(meta_dict, indent=2, ensure_ascii=False), encoding="utf-8")

        log.info(f"PDF manual ingested successfully: {md_path} ({len(markdown.split())} words)")
        return md_path, meta_path, markdown
