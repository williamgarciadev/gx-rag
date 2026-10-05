"""CLI to ingest local documentation files (PDF, Markdown) into a collection.

Usage:
    python -m scripts.ingest_local <path>... [--collection bt_docs] \\
        [--limit N] [--dry-run]
    python -m scripts.ingest_local --manifest data/manuals.json [--dry-run]

Paths may be files or directories (swept recursively). --manifest ingests
exactly what `data/manuals.json` flags, which is the only place recording *why*
a document is in or out — a directory sweep cannot express that, and a folder
named `_skipped/` turned out to be indexed anyway.

--dry-run parses and reports WITHOUT indexing — the human gate the project
requires before any corpus growth.
"""

import argparse
import itertools
import sys
from pathlib import Path

from scripts._console import use_utf8_stdout
from src.ingestion.bulk_loader import RawDoc, load_dump
from src.ingestion.local_loader import iter_supported_files, parse_local_docs
from src.ingestion.manifest import iter_manifest_paths, load_manifest
from src.vectorstore.indexer import CHUNK_SIZE


def _expand(paths: list[str]):
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            yield from iter_supported_files(path)
        else:
            yield path


def _iter_docs(paths, limit: int | None):
    docs = parse_local_docs(paths)
    return itertools.islice(docs, limit) if limit is not None else docs


def _dry_run(docs, collection: str) -> None:
    total = est_chunks = 0
    samples: list[RawDoc] = []
    for doc in docs:
        total += 1
        est_chunks += max(1, len(doc.content) // CHUNK_SIZE)
        if len(samples) < 5:
            samples.append(doc)
    print(f"[dry-run] documentos: {total}")
    print(f"[dry-run] chunks estimados (~{CHUNK_SIZE}c): ~{est_chunks}")
    print(f"[dry-run] destino: {collection} — NO se indexa nada")
    print("[dry-run] muestras:")
    for d in samples:
        page = f"p.{d.page_id}" if d.page_id else "—"
        print(f"    - [{page}] {d.title[:60]}  ({len(d.content)} chars)")

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Ingest local docs (PDF/Markdown).")
    p.add_argument("paths", nargs="*", help="Files or directories to ingest")
    p.add_argument("--manifest", help="Manifest declaring what to ingest and why")
    p.add_argument("--root", help="Override the manifest's document root")
    p.add_argument("--collection", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    use_utf8_stdout()

    if args.manifest:
        manifest = load_manifest(args.manifest)
        collection = args.collection or manifest.collection
        paths = iter_manifest_paths(args.manifest, root=args.root)
        skipped = [d for d in manifest.documents if not d.ingest]
        print(f"[manifest] {args.manifest} → colección '{collection}'")
        print(f"[manifest] declarados: {len(manifest.documents)} · fuera: {len(skipped)}")
        for entry in skipped:
            print(f"    - FUERA {entry.file}: {entry.reason[:90]}")
    elif args.paths:
        collection = args.collection or "bt_docs"
        paths = _expand(args.paths)
    else:
        p.error("pasá rutas o --manifest")

    docs = _iter_docs(paths, args.limit)

    if args.dry_run:
        _dry_run(docs, collection)
        return 0

    def _progress(i: int, doc: RawDoc, status: str) -> None:
        if (i + 1) % 50 == 0:
            print(f"  ... {i + 1} procesados", file=sys.stderr)

    report = load_dump(docs, collection=collection, on_progress=_progress)
    print(
        f"OK — total={report.total} indexed={report.indexed} "
        f"reindexed={report.reindexed} skipped={report.skipped} "
        f"-> colección '{collection}'"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
