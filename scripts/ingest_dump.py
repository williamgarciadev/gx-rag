"""CLI to bulk-load a pre-scraped documentation dump into a retrieval collection.

Usage:
    python -m scripts.ingest_dump <file> --format genexus|workwithplus \\
        [--collection gx_docs] [--limit N] [--dry-run]

--dry-run parses and reports (counts, samples, estimated chunks) WITHOUT
indexing — a preview gate before committing thousands of docs to ChromaDB.
"""

import argparse
import itertools
import sys

from scripts._console import use_utf8_stdout
from src.ingestion.bulk_loader import (
    RawDoc,
    load_dump,
    parse_genexus_dump,
    parse_wwp_dump,
)

_PARSERS = {"genexus": parse_genexus_dump, "workwithplus": parse_wwp_dump}
_CHUNK_SIZE = 800  # mirrors indexer.CHUNK_SIZE for the dry-run estimate


def _iter_docs(path: str, fmt: str, limit: int | None):
    text = open(path, encoding="utf-8").read()
    docs = _PARSERS[fmt](text)
    if limit is not None:
        docs = itertools.islice(docs, limit)
    return docs


def _dry_run(docs) -> None:
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
    print("[dry-run] destino: gx_docs (retrieval) — NO se indexa nada")
    print("[dry-run] muestras:")
    for d in samples:
        print(f"    - [{d.page_id}] {d.title[:60]}  ({len(d.content)} chars)  {d.url}")

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Bulk-load a documentation dump.")
    p.add_argument("file", help="Path to the dump .txt file")
    p.add_argument("--format", required=True, choices=list(_PARSERS))
    p.add_argument("--collection", default="gx_docs")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    use_utf8_stdout()

    docs = _iter_docs(args.file, args.format, args.limit)

    if args.dry_run:
        _dry_run(docs)
        return 0

    def _progress(i: int, doc: RawDoc, status: str) -> None:
        if (i + 1) % 50 == 0:
            print(f"  ... {i + 1} procesados", file=sys.stderr)

    report = load_dump(docs, collection=args.collection, on_progress=_progress)
    print(
        f"OK — total={report.total} indexed={report.indexed} "
        f"reindexed={report.reindexed} skipped={report.skipped} "
        f"→ colección '{args.collection}'"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
