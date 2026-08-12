"""CLI for the human-gated syntax-candidate curation loop.

    python -m scripts.candidates extract <file> --format genexus|workwithplus [--dry-run]
    python -m scripts.candidates list [--status pending]
    python -m scripts.candidates promote <id>
    python -m scripts.candidates reject <id>

`extract` discovers candidates (automatic, allowed). `promote` is the human
action that writes an authoritative catalog row — nothing reaches gx_syntax
without it.
"""

import argparse

from src.ingestion.bulk_loader import parse_genexus_dump, parse_wwp_dump
from src.syntax_db import candidate_repository as cr
from src.syntax_db.extractor import extract_candidates, store_drafts

_PARSERS = {"genexus": parse_genexus_dump, "workwithplus": parse_wwp_dump}


def _cmd_extract(args) -> int:
    text = open(args.file, encoding="utf-8").read()
    drafts = extract_candidates(_PARSERS[args.format](text))
    print(f"candidatos detectados: {len(drafts)}")
    for d in drafts[:10]:
        print(f"    - {d.name} [{d.object_type}] :: {d.signature}")
    if args.dry_run:
        print("[dry-run] no se guardó nada en staging")
        return 0
    stored = store_drafts(drafts)
    print(f"guardados en staging (pending): {stored}")
    return 0


def _cmd_list(args) -> int:
    rows = cr.list_candidates(status=args.status)
    print(f"candidatos ({args.status}): {len(rows)}")
    for r in rows:
        print(
            f"    #{r['id']} {r['name']} [{r['object_type']}] hits={r['hit_count']} :: {r['signature']}"
        )
    return 0


def _cmd_promote(args) -> int:
    result = cr.promote_candidate(args.id)
    if not result:
        print(f"no se pudo promover #{args.id} (inexistente o ya promovido)")
        return 1
    print(f"promovido a gx_syntax: {result}")
    return 0


def _cmd_reject(args) -> int:
    ok = cr.reject_candidate(args.id)
    print(f"rechazado #{args.id}" if ok else f"no encontrado #{args.id}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Curación de candidatos de sintaxis.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("extract", help="Descubrir candidatos desde un dump")
    pe.add_argument("file")
    pe.add_argument("--format", required=True, choices=list(_PARSERS))
    pe.add_argument("--dry-run", action="store_true")
    pe.set_defaults(func=_cmd_extract)

    pl = sub.add_parser("list", help="Listar candidatos por estado")
    pl.add_argument("--status", default="pending")
    pl.set_defaults(func=_cmd_list)

    pp = sub.add_parser("promote", help="Promover un candidato al catálogo")
    pp.add_argument("id", type=int)
    pp.set_defaults(func=_cmd_promote)

    pr = sub.add_parser("reject", help="Rechazar un candidato")
    pr.add_argument("id", type=int)
    pr.set_defaults(func=_cmd_reject)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
