"""¿Se encuentra cada ETP ingerido, buscándolo por su tema y por su código de programa?

Usage:
    python -m scripts.eval_etp_titles [--top-k 10] [--json out.json] [--limit N]

Complementa eval/gold.jsonl, que tiene 12 casos escritos a mano y ninguno de ETP.
Aquí las preguntas salen del título de cada ETP en bt_docs ("Cierre Forzoso de Caja
(HMBC024)"): el TEMA, como lo buscaría un funcional, y cada CÓDIGO de programa, como
lo busca un desarrollador. Medido el 2026-10-08: "Novación de Préstamos Ppp93051"
no traía su ETP ni en el top-20, porque la mitad léxica no pondera la rareza del
código. Esta prueba da el número de antes y de después de arreglarlo.

La métrica es ACIERTO@k (algún capítulo del ETP en el top-k), no el Recall@k de
eval_retrieval: un ETP de 5 capítulos encontrado por uno solo daría 0,2 allí.

Límite: son preguntas más fáciles que las de una persona (repiten el título). Si
falla aquí, falla de verdad; si pasa, no garantiza que pase con preguntas reales.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from scripts._console import use_utf8_stdout

#: Un código de programa Bantotal: letras y luego dígitos (HMBC024, PRICO001, PFS99508).
_CODE_RE = re.compile(r"\b[A-Za-z]{2,}\d+[A-Za-z0-9]*\b")
_PARENS_RE = re.compile(r"\(([^()]*)\)")
_KS = (1, 5, 10)


def questions_from_title(title: str) -> tuple[str, list[str]]:
    """(tema, [códigos]) de un título "<tema> (<programas>)".

    Los códigos salen del ÚLTIMO paréntesis que contenga alguno; un paréntesis sin
    códigos ("(Novación de Garantía)") es parte del tema.
    """
    for match in reversed(list(_PARENS_RE.finditer(title))):
        codes = _CODE_RE.findall(match.group(1))
        if codes:
            topic = title[: match.start()] + title[match.end():]
            return " ".join(topic.split()), codes
    return " ".join(title.split()), []


def hit_rate(ranks: list[int | None], k: int) -> float:
    """Fracción de casos con algún capítulo esperado en el top-k."""
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks)


def _etp_documents(conn) -> list[tuple[str, str, frozenset[str]]]:
    """(manual, título, article_ids) de cada ETP de bt_docs."""
    rows = conn.execute(
        """SELECT meta->>'manual', meta->>'manual_title', array_agg(DISTINCT article_id)
             FROM chunks
            WHERE collection = 'bt_docs' AND meta->>'manual' LIKE 'ETP-%%'
            GROUP BY source, meta->>'manual', meta->>'manual_title'
            ORDER BY 1, 2"""
    ).fetchall()
    return [(manual, title, frozenset(ids)) for manual, title, ids in rows if title]


def _cases(documents):
    from src.evaluation.gold import GoldCase

    by_mode: dict[str, list] = {"tema": [], "código": []}
    for manual, title, ids in documents:
        topic, codes = questions_from_title(title)
        note = f"{manual} — {title}"
        by_mode["tema"].append(GoldCase(topic, "bt_docs", ids, note))
        by_mode["código"].extend(GoldCase(code, "bt_docs", ids, note) for code in codes)
    return by_mode


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--limit", type=int, help="solo los primeros N ETP (prueba rápida)")
    args = parser.parse_args(argv)

    from src.evaluation.runner import run_gold
    from src.vectorstore.pg_client import close_pool, get_pool
    from src.vectorstore.pg_retriever import hybrid_search

    try:
        with get_pool().connection() as conn:
            documents = _etp_documents(conn)[: args.limit]
        report = {"etp": len(documents), "top_k": args.top_k, "modos": {}}
        for mode, cases in _cases(documents).items():
            results, summary = run_gold(cases, args.top_k, _KS, hybrid_search)
            ranks = [r.rank for r in results]
            stats = {f"acierto@{k}": round(hit_rate(ranks, k), 3) for k in _KS}
            stats["mrr"] = round(summary.mrr, 3)
            stats["casos"] = len(cases)
            stats["fallos"] = [c.note for c, r in zip(cases, results) if r.missed]
            report["modos"][mode] = stats
            print(f"{mode:7} {len(cases):4} casos · " + " · ".join(
                f"{k} {v:.3f}" for k, v in stats.items() if k.startswith(("acierto", "mrr"))))
    finally:
        close_pool()
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
