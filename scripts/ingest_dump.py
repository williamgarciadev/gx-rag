"""CLI to bulk-load pre-scraped documentation into a retrieval collection.

Usage:
    python -m scripts.ingest_dump <file> --format genexus|workwithplus \\
        [--collection gx_docs] [--limit N] [--dry-run]

    python -m scripts.ingest_dump <directory> --format files \\
        [--glob '*.txt'] [--id-prefix bt/manual-funcional] [--dry-run]

--dry-run parses and reports (counts, samples, estimated chunks) WITHOUT
indexing — a preview gate before committing thousands of docs to the store.

Note the dry-run chunk count is an ESTIMATE from content length, so it cannot
detect a document that bypasses the splitter entirely. That failure mode is
caught by the G1 guardrail query against the database, after writing.
"""

import argparse
import itertools
import sys
from pathlib import Path

from src.ingestion.bulk_loader import (
    RawDoc,
    load_dump,
    parse_genexus_dump,
    parse_text_files,
    parse_wwp_dump,
    split_index_files,
)

_PARSERS = {"genexus": parse_genexus_dump, "workwithplus": parse_wwp_dump}

# `files` does not parse a blob of delimited text: it takes a directory whose
# files are already one document each, so it is dispatched separately.
_FILES_FORMAT = "files"
_FORMATS = [*_PARSERS, _FILES_FORMAT]

# Defaults for the corpus this format was built for: the Bantotal functional
# manual, 35 chapters exported from Word to plain text.
_DEFAULT_ID_PREFIX = "bt/manual-funcional"
_DEFAULT_TITLE_PREFIX = "Manual Funcional Bantotal — "

_CHUNK_SIZE = 800  # mirrors indexer.CHUNK_SIZE for the dry-run estimate


def _iter_docs(path: str, fmt: str, limit: int | None, args=None):
    if fmt == _FILES_FORMAT:
        docs = _iter_file_docs(path, args)
    else:
        text = open(path, encoding="utf-8-sig").read()
        docs = _PARSERS[fmt](text)
    if limit is not None:
        docs = itertools.islice(docs, limit)
    return docs


def _iter_file_docs(path: str, args):
    """Resolve a directory (or single file) into RawDocs, reporting what was skipped."""
    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(path)
    paths = sorted(root.glob(args.glob)) if root.is_dir() else [root]
    if not paths:
        print(f"[warn] '{args.glob}' no matcheó ningún archivo en {root}", file=sys.stderr)

    content, indexes = split_index_files(paths)
    for p in indexes:
        print(f"[info] índice de volumen, NO se indexa como contenido: {p.name}")
    return parse_text_files(
        content, id_prefix=args.id_prefix, title_prefix=args.title_prefix
    )


def _dry_run(docs, collection: str) -> None:
    total = 0
    est_chunks = 0
    samples: list[RawDoc] = []
    for doc in docs:
        total += 1
        est_chunks += max(1, len(doc.content) // _CHUNK_SIZE)
        if len(samples) < 5:
            samples.append(doc)
    print(f"[dry-run] documentos: {total}")
    print(f"[dry-run] chunks estimados (~{_CHUNK_SIZE}c): ~{est_chunks}")
    print(f"[dry-run] destino: {collection} (genre 'guide') — NO se indexa nada")
    print("[dry-run] muestras:")
    for d in samples:
        print(f"    - [{d.page_id}] {d.title[:60]}  ({len(d.content)} chars)  {d.url}")
    if total:
        print(
            "[dry-run] para escribir de verdad, repetí el comando sin --dry-run. "
            "Es idempotente: lo ya indexado sin cambios se saltea."
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Bulk-load documentation into the store.")
    p.add_argument("file", help="Dump .txt file, or a directory when --format files")
    p.add_argument("--format", required=True, choices=_FORMATS)
    p.add_argument("--collection", default="gx_docs")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--glob", default="*.txt", help="[files] pattern within the directory"
    )
    p.add_argument(
        "--id-prefix",
        default=_DEFAULT_ID_PREFIX,
        help="[files] article id prefix; changing it orphans previously indexed rows",
    )
    p.add_argument("--title-prefix", default=_DEFAULT_TITLE_PREFIX, help="[files]")
    args = p.parse_args(argv)

    docs = _iter_docs(args.file, args.format, args.limit, args)

    if args.dry_run:
        _dry_run(docs, args.collection)
        return 0

    def _progress(i: int, doc: RawDoc, status: str) -> None:
        if (i + 1) % 50 == 0:
            print(f"  ... {i + 1} procesados", file=sys.stderr)

    report = load_dump(docs, collection=args.collection, on_progress=_progress)
    # ASCII a propósito: la consola de Windows usa cp1252, donde '→' (->) no
    # existe y el print revienta con UnicodeEncodeError DESPUÉS de haber indexado
    # todo. Se pierde exactamente el reporte que prueba la idempotencia.
    print(
        f"OK - total={report.total} indexed={report.indexed} "
        f"reindexed={report.reindexed} skipped={report.skipped} "
        f"-> coleccion '{args.collection}'"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
