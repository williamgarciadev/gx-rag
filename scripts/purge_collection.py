"""CLI to delete chunks from a collection by metadata match.

Usage:
    python -m scripts.purge_collection <collection> \\
        --field id --contains MDU-99000 [--dry-run]

Deliberately a separate CLI from `split_collection.py` rather than a `--delete`
flag on it: a flag that turns "move" into "irreversible delete" is one keystroke
away from being typed by accident.

The canonical case: MDU-99000 (the table-inventory manual) is 42% of bt_docs and
duplicates what the bantotal-dev MCP answers from the live schema — it only
competes for the same top-k.

    python -m scripts.purge_collection bt_docs --field id --contains MDU-99000 --dry-run

--dry-run counts matches WITHOUT deleting — the human gate the project requires
before any bulk corpus operation. ALWAYS run it first: the delete is only
undoable by re-ingesting the source.
"""

import argparse
import sys

from src.vectorstore.chroma_client import get_client
from src.vectorstore.migrations import (
    count_matching_by_metadata,
    delete_matching_by_metadata,
)

from scripts._console import use_utf8_stdout


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Delete chunks from a collection.")
    p.add_argument("collection")
    p.add_argument("--field", default="id", help="Metadata field to match on")
    p.add_argument("--contains", required=True, help="Substring the field must contain")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    use_utf8_stdout()

    client = get_client()
    match = dict(field=args.field, contains=args.contains)
    total = client.get_or_create_collection(args.collection).count()

    if args.dry_run:
        n = count_matching_by_metadata(client, args.collection, **match)
        print(f"[dry-run] '{args.collection}' tiene {total} chunks")
        print(f"[dry-run] coinciden {args.field} ~ '{args.contains}': {n}")
        print(f"[dry-run] quedarían {total - n} — NO se borra nada")
        return 0

    def _progress(deleted: int) -> None:
        if deleted % 2000 < 500:
            print(f"  ... {deleted} borrados", file=sys.stderr)

    deleted = delete_matching_by_metadata(
        client, args.collection, on_progress=_progress, **match
    )
    left = client.get_or_create_collection(args.collection).count()
    print(f"OK — borrados={deleted} · '{args.collection}' queda con {left}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
