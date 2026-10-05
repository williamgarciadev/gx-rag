#!/usr/bin/env python3
"""Populate gx_syntax from the verified GeneXus corpus catalogs.

This is the 'cut' layer of the two-layer anti-hallucination design: retrieval
mitigates, a closed catalog cuts. The table has been empty since the project
started, so every verdict was NO_VERIFICABLE in practice.

Dry-run by default. Writing to the catalog is corpus growth, and the project's
rule is that automatic discovery is fine while automatic ingestion is not —
you have to ask for --apply.

    .venv/Scripts/python.exe scripts/load_catalog.py            # preview
    .venv/Scripts/python.exe scripts/load_catalog.py --apply

The sources carry no version column, so every row lands with version_since
NULL. That is not an oversight: verify_syntax answers PISTA for a version older
than the catalog's own, which is the honest verdict for a GX16 Bantotal KB.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._console import use_utf8_stdout  # noqa: E402
from src.syntax_db.catalog_loader import (  # noqa: E402
    DEFAULT_OBJECT_TYPES,
    load_entries,
    parse_catalog_tsv,
)

DEFAULT_CORPUS = Path.home() / ".claude" / "genexus" / "corpus"
API_FILE = "api.tsv"


def collect(corpus: Path):
    """Every entry across the four catalog files, with its provenance."""
    entries = []
    api = corpus / API_FILE
    if api.is_file():
        entries.extend(parse_catalog_tsv(api))  # 3 columns: type per row
    else:
        print(f"  aviso: no está {api}")

    for filename, object_type in DEFAULT_OBJECT_TYPES.items():
        path = corpus / filename
        if not path.is_file():
            print(f"  aviso: no está {path}")
            continue
        entries.extend(parse_catalog_tsv(path, object_type=object_type))
    return entries


def main() -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument(
        "--apply", action="store_true", help="write to the catalog (default: preview)"
    )
    args = parser.parse_args()

    print(f"Corpus: {args.corpus}")
    entries = collect(args.corpus)
    if not entries:
        print("Nada que cargar.")
        return 1

    by_type: dict[str, int] = {}
    multiword = 0
    for entry in entries:
        by_type[entry.object_type] = by_type.get(entry.object_type, 0) + 1
        if " " in entry.name:
            multiword += 1

    print(f"\n{len(entries)} entradas:")
    for object_type, count in sorted(by_type.items(), key=lambda kv: -kv[1]):
        print(f"  {object_type:12s} {count:5d}")
    print(
        f"\n{multiword} nombres multi-palabra: alcanzables por list_methods y por "
        "las sugerencias, NO por la coincidencia exacta de verify_syntax."
    )

    if not args.apply:
        print("\nDRY RUN — nada se escribió. Repite con --apply.")
        return 0

    report = load_entries(entries)
    print(f"\nInsertadas {report.inserted}, ya presentes {report.skipped}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
