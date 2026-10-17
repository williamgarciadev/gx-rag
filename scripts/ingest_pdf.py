"""CLI for ingesting Bantotal technical and functional manual PDFs into RAG documentation.

Usage:
    python -m scripts.ingest_pdf <pdf_file_or_dir> [options]

Options:
    --output-dir DIR    Directory where .md and .meta.json will be saved (default: data/manuales_processed)
    --module NAME       Bantotal business module (e.g., Cuentas, Préstamos, Contabilidad)
    --title TITLE       Custom title for single file ingestion
    --collection NAME   Target vector collection (default: bt_docs)
    --index             Automatically index into PostgreSQL vectorstore
    --dry-run           Parse and display preview without saving files or writing to DB
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts._console import use_utf8_stdout
from src.collection_policy import add_collection_argument
from src.ingestion.pdf_pipeline import PDFIngestPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest Bantotal manual PDFs into high-density Markdown for RAG."
    )
    parser.add_argument("path", type=str, help="Path to PDF file or directory of PDFs")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/manuales_processed",
        help="Directory to save generated .md and metadata (default: data/manuales_processed)",
    )
    parser.add_argument(
        "--module",
        type=str,
        default="Bantotal General",
        help="Bantotal business module name (e.g. Cuentas, Préstamos, Contabilidad)",
    )
    parser.add_argument(
        "--title",
        type=str,
        default=None,
        help="Custom title (only applies when ingesting a single file)",
    )
    add_collection_argument(parser, default="bt_docs", help="Target collection (unused: use ingest_local --manifest to index)")
    parser.add_argument(
        "--index",
        action="store_true",
        help="DEPRECATED: La indexación directa fue retirada. Usa scripts.ingest_local --manifest.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate preview and stats without persisting to disk",
    )
    return parser


def process_single_pdf(
    pdf_file: Path,
    output_dir: Path,
    module: str,
    title: str | None,
    dry_run: bool,
    pipeline: PDFIngestPipeline,
) -> int:
    print(f"\n==> Processing PDF: {pdf_file.name}")
    print(f"    Module: {module}")

    if dry_run:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            md_path, meta_path, markdown = pipeline.process(
                pdf_path=pdf_file,
                output_dir=tmp_path,
                title=title,
                module=module,
            )
            print("\n[DRY-RUN PREVIEW]")
            print(f"Generated Markdown length: {len(markdown)} characters ({len(markdown.split())} words)")
            lines = markdown.splitlines()
            print("\nFirst 40 lines of generated Markdown:")
            print("-" * 60)
            for line in lines[:40]:
                print(line)
            print("-" * 60)
            return 0

    md_path, meta_path, markdown = pipeline.process(
        pdf_path=pdf_file,
        output_dir=output_dir,
        title=title,
        module=module,
    )

    print(f"[SUCCESS] Generated: {md_path}")
    print(f"          Metadata:  {meta_path}")
    return 0


def main() -> int:
    use_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args()

    if args.index:
        print(
            "Error: La opción '--index' fue retirada de ingest_pdf para evitar identidades duplicadas (#10, #11).\n"
            "ingest_pdf solo genera Markdown estructurado (.md y .meta.json).\n"
            "Para indexar en la base de datos, declara el documento en data/manuals.json y corre:\n"
            "    python -m scripts.ingest_local --manifest data/manuals.json",
            file=sys.stderr,
        )
        return 1

    target_path = Path(args.path).resolve()
    if not target_path.exists():
        print(f"Error: Path not found: {target_path}", file=sys.stderr)
        return 1

    pipeline = PDFIngestPipeline()
    output_dir = Path(args.output_dir)

    if target_path.is_file():
        return process_single_pdf(
            pdf_file=target_path,
            output_dir=output_dir,
            module=args.module,
            title=args.title,
            dry_run=args.dry_run,
            pipeline=pipeline,
        )

    # Directory of PDFs
    pdf_files = sorted(target_path.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDF files found in directory: {target_path}", file=sys.stderr)
        return 1

    print(f"Found {len(pdf_files)} PDF files in {target_path}")
    failed = 0
    for pdf_file in pdf_files:
        res = process_single_pdf(
            pdf_file=pdf_file,
            output_dir=output_dir,
            module=args.module,
            title=None,
            dry_run=args.dry_run,
            pipeline=pipeline,
        )
        if res != 0:
            failed += 1

    print(f"\nIngestion finished. Processed: {len(pdf_files) - failed}, Failed: {failed}")
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
