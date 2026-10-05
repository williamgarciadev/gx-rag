#!/usr/bin/env python3
"""Score the retriever against the gold set.

The number this project did not have. Run it BEFORE and AFTER any change to
chunking, the embedding model, the e5 prefixes, RRF_K, CANDIDATE_POOL or the
per-article cap — otherwise the change is a hypothesis, not an improvement.

    .venv/Scripts/python.exe scripts/eval_retrieval.py
    .venv/Scripts/python.exe scripts/eval_retrieval.py --top-k 10 --json out.json

--json writes the full report to a file: the console view is a summary, and a
saved run is what you diff against after a change.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._console import use_utf8_stdout  # noqa: E402
from src.evaluation.gold import load_gold  # noqa: E402
from src.evaluation.runner import run_gold  # noqa: E402
from src.paths import REPO_ROOT  # noqa: E402

DEFAULT_GOLD = REPO_ROOT / "eval" / "gold.jsonl"
# Retrieve deeper than the recall cutoffs so a near-miss reports its real rank
# instead of collapsing to "not found" — "rank 7" and "absent" call for
# different fixes (reranking vs. recall).
RETRIEVE_DEPTH = 20


def _load_and_run(gold_path: Path, top_k: int, ks: tuple[int, ...]):
    """Wire the real retriever to the gold set. Import is lazy: loading the
    embedding model costs ~18s, and `--help` should not pay it."""
    from src.vectorstore.retriever import hybrid_search

    cases = load_gold(gold_path)
    results, report = run_gold(cases, top_k, ks, hybrid_search)
    return cases, results, report


def _render(report, results, ks) -> str:
    lines = [
        "=" * 68,
        f"RETRIEVAL EVAL  ({report.total} casos)",
        "=" * 68,
    ]
    for k in ks:
        lines.append(f"  Recall@{k:<3} {report.recall[k]:.3f}")
    lines.append(f"  MRR       {report.mrr:.3f}")
    lines.append("")
    lines.append("Por caso (rank del primer acierto, '-' = no recuperado):")
    for result in results:
        mark = "OK " if not result.missed else "MISS"
        rank = result.rank if result.rank else "-"
        lines.append(f"  [{mark}] rank={rank!s:<4} {result.question}")
    if report.misses:
        lines.append("")
        lines.append(f"{len(report.misses)} caso(s) sin recuperar el documento esperado.")
    return "\n".join(lines)


def main() -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument(
        "--top-k",
        type=int,
        default=RETRIEVE_DEPTH,
        help="how deep to retrieve before scoring (default: %(default)s)",
    )
    parser.add_argument("--ks", default="1,5,10", help="recall cutoffs")
    parser.add_argument("--json", type=Path, help="also write the report as JSON")
    args = parser.parse_args()

    ks = tuple(int(k) for k in args.ks.split(","))
    cases, results, report = _load_and_run(args.gold, args.top_k, ks)

    print(_render(report, results, ks))

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "total": report.total,
                    "recall": {str(k): v for k, v in report.recall.items()},
                    "mrr": report.mrr,
                    "cases": [
                        {
                            "question": r.question,
                            "expected": sorted(r.expected),
                            "collection": c.collection,
                            "rank": r.rank,
                            "recall": {str(k): v for k, v in r.recall.items()},
                        }
                        for c, r in zip(cases, results)
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    # Non-zero when any case missed, so this can gate a change later.
    return 1 if report.misses else 0


if __name__ == "__main__":
    raise SystemExit(main())
