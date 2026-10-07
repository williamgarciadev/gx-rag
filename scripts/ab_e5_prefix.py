#!/usr/bin/env python3
"""A/B the e5 prefixes on a subset, before committing ~5h of CPU to gx_docs.

Rebuilding gx_docs costs 5h32m at the measured 1.1 chunks/s on this machine.
Paying that to find out whether a hypothesis holds is the mistake this project
kept making — a change adopted because it sounded right, with no number either
way. So: same chunks, same size, one variable.

    ARM A (control)    stored vectors COPIED, no embedding at all — the
                       migrations.py trick. Raw text on both sides, i.e. today.
    ARM B (treatment)  those same chunks re-embedded as "passage: ...", queried
                       as "query: ...".

Only arm B pays for embedding, so the run costs ~(sample size / 1.1) seconds.

    .venv/Scripts/python.exe scripts/ab_e5_prefix.py --sample 1100
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.vectorstore.chroma_client import get_client  # noqa: E402

from scripts._console import use_utf8_stdout  # noqa: E402
from src.evaluation.gold import load_gold  # noqa: E402
from src.evaluation.runner import run_gold  # noqa: E402
from src.paths import REPO_ROOT  # noqa: E402
from src.vectorstore import embeddings as emb  # noqa: E402

SOURCE = "gx_docs"
# Chroma requires 3-512 chars from [a-zA-Z0-9._-] starting and ending
# alphanumeric — a leading underscore is rejected outright.
CONTROL = "ab-control"
TREATMENT = "ab-treatment"
WRITE_BATCH = 200


def _all_rows(col, include):
    """Page through a whole collection — an unbounded get() overflows SQLite."""
    rows, offset = [], 0
    while True:
        page = col.get(limit=1000, offset=offset, include=include)
        ids = page.get("ids") or []
        if not ids:
            return rows
        for i, chunk_id in enumerate(ids):
            rows.append(
                (
                    chunk_id,
                    page["documents"][i],
                    page["metadatas"][i] or {},
                    page["embeddings"][i] if "embeddings" in include else None,
                )
            )
        offset += len(ids)


def _select(rows, expected: set[str], sample: int):
    """Every chunk of the expected articles, plus distractors up to `sample`.

    The expected articles must be present or the case is unscorable by
    construction; the distractors are what make a rank mean anything. Both arms
    get the identical selection, so size is controlled and the prefix is the
    only difference.
    """
    wanted = [r for r in rows if r[2].get("id") in expected]
    others = [r for r in rows if r[2].get("id") not in expected]
    # Deterministic stride instead of random: two runs must be comparable.
    step = max(1, len(others) // max(1, sample))
    return wanted + others[::step][:sample]


def _write(client, name, rows, embed_fn):
    col = client.get_or_create_collection(name)
    for start in range(0, len(rows), WRITE_BATCH):
        batch = rows[start : start + WRITE_BATCH]
        col.add(
            ids=[r[0] for r in batch],
            documents=[r[1] for r in batch],
            metadatas=[r[2] for r in batch],
            embeddings=embed_fn([r[1] for r in batch], batch),
        )
        print(f"    {min(start + WRITE_BATCH, len(rows))}/{len(rows)}", flush=True)
    return col


def _search_with(collection, embed_query_fn):
    """A hybrid_search bound to one collection and one query-embedding function."""
    from src.vectorstore import retriever

    def _fn(query, collection_ignored=None, top_k=20, **kw):
        original = retriever.emb.embed
        retriever.emb.embed = embed_query_fn
        try:
            return retriever.hybrid_search(query, collection=collection, top_k=top_k)
        finally:
            retriever.emb.embed = original

    return _fn


def main() -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=1100)
    parser.add_argument("--gold", type=Path, default=REPO_ROOT / "eval" / "gold.jsonl")
    args = parser.parse_args()

    cases = [c for c in load_gold(args.gold) if c.collection == SOURCE]
    expected = {e for c in cases for e in c.expected}
    print(f"{len(cases)} casos de {SOURCE}, {len(expected)} artículos esperados")

    client = get_client()
    for name in (CONTROL, TREATMENT):
        try:
            client.delete_collection(name)
        except Exception:
            pass

    print("Leyendo corpus…", flush=True)
    rows = _all_rows(
        client.get_or_create_collection(SOURCE),
        ["documents", "metadatas", "embeddings"],
    )
    selected = _select(rows, expected, args.sample)
    print(f"Subconjunto: {len(selected)} chunks (de {len(rows)})")

    print("ARM A control — copiando vectores existentes (sin embeber)…", flush=True)
    _write(client, CONTROL, selected, lambda _texts, batch: [list(r[3]) for r in batch])

    print(f"ARM B tratamiento — embebiendo {len(selected)} chunks con 'passage: '…")
    started = time.time()
    _write(
        client,
        TREATMENT,
        selected,
        lambda texts, _batch: emb.embed_passages(tuple(texts)).tolist(),
    )
    print(f"  embebido en {time.time() - started:.0f}s")

    ks = (1, 5, 10)
    _, control = run_gold(cases, 20, ks, _search_with(CONTROL, emb.embed))
    _, treatment = run_gold(cases, 20, ks, _search_with(TREATMENT, emb.embed_query))

    print("\n" + "=" * 60)
    print(f"A/B PREFIJOS E5  ({len(cases)} casos, {len(selected)} chunks/brazo)")
    print("=" * 60)
    print(f"{'métrica':<12}{'A control':>12}{'B prefijos':>12}{'delta':>10}")
    for k in ks:
        a, b = control.recall[k], treatment.recall[k]
        print(f"{'Recall@' + str(k):<12}{a:>12.3f}{b:>12.3f}{b - a:>+10.3f}")
    a, b = control.mrr, treatment.mrr
    print(f"{'MRR':<12}{a:>12.3f}{b:>12.3f}{b - a:>+10.3f}")
    print(f"\nmisses  A={len(control.misses)}  B={len(treatment.misses)}")
    print(f"\nColecciones temporales: {CONTROL}, {TREATMENT} (bórralas cuando termines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
