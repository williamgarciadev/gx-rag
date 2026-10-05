"""CLI to split a collection by chunk provenance, reusing stored embeddings.

Usage:
    python -m scripts.split_collection <source> <target> \\
        --field url --contains sac.genexus.com [--dry-run]

The canonical case: pulling sac.genexus.com support tickets out of gx_docs so
reported-but-unresolved issues stop competing with the official wiki for the
same top-k.

    python -m scripts.split_collection gx_docs gx_sac \\
        --field url --contains sac.genexus.com --dry-run

--dry-run counts matches WITHOUT moving anything — the human gate the project
requires before any bulk corpus operation. The move is idempotent: re-running
after an interruption converges instead of duplicating.
"""

import argparse
import sys

from scripts._console import use_utf8_stdout
from src.vectorstore.chroma_client import get_client
from src.vectorstore.migrations import (
    count_matching_by_metadata,
    move_matching_by_metadata,
)

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Split a collection by metadata match.")
    p.add_argument("source")
    p.add_argument("target")
    p.add_argument("--field", default="url", help="Metadata field to match on")
    p.add_argument("--contains", required=True, help="Substring the field must contain")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    use_utf8_stdout()

    client = get_client()
    match = dict(field=args.field, contains=args.contains)

    if args.dry_run:
        n = count_matching_by_metadata(client, args.source, **match)
        total = client.get_or_create_collection(args.source).count()
        print(f"[dry-run] '{args.source}' tiene {total} chunks")
        print(f"[dry-run] coinciden {args.field} ~ '{args.contains}': {n}")
        print(f"[dry-run] quedarían {total - n} en '{args.source}'")
        print(f"[dry-run] destino: {args.target} — NO se mueve nada")
        return 0

    def _progress(moved: int) -> None:
        if moved % 2000 < 500:
            print(f"  ... {moved} movidos", file=sys.stderr)

    moved = move_matching_by_metadata(
        client, args.source, args.target, on_progress=_progress, **match
    )
    src_left = client.get_or_create_collection(args.source).count()
    dst_total = client.get_or_create_collection(args.target).count()
    print(
        f"OK — movidos={moved} · '{args.source}' queda con {src_left} · "
        f"'{args.target}' tiene {dst_total}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
