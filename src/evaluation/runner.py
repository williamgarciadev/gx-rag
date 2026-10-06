"""Run the gold set through a retriever and score it.

Separated from the CLI script so the logic is testable without a vector store,
a model, or 20 seconds of startup — the same reason the metrics are pure
functions. A harness that can only be exercised end-to-end gets run once.
"""

from collections.abc import Callable

from src.evaluation.gold import GoldCase
from src.evaluation.metrics import CaseResult, Report, evaluate_case, summarize


def ranked_article_ids(
    case: GoldCase, top_k: int, search_fn: Callable[..., list[dict]]
) -> list[str]:
    """Article ids for one case, best first, each appearing once.

    Retrieval ranks CHUNKS and the gold set names DOCUMENTS. Without the
    de-duplication, three chunks of one manual chapter would occupy three of the
    five ranks and Recall@5 would be measuring the per-article cap rather than
    the retriever.

    Falls back to the chunk id when metadata carries no article id, so a
    malformed row is still rankable instead of vanishing and understating recall.
    """
    ordered: list[str] = []
    hits = search_fn(case.question, collection=case.collection, top_k=top_k,
                     filters=case.filters)
    for hit in hits:
        article = hit.get("id") or hit.get("doc_id", "")
        if article and article not in ordered:
            ordered.append(article)
    return ordered


def run_gold(
    cases: list[GoldCase],
    top_k: int,
    ks: tuple[int, ...],
    search_fn: Callable[..., list[dict]],
) -> tuple[list[CaseResult], Report]:
    """Score every gold case, returning the per-case results and the summary."""
    results = [
        evaluate_case(
            case.question,
            set(case.expected),
            ranked_article_ids(case, top_k, search_fn),
            ks=ks,
        )
        for case in cases
    ]
    return results, summarize(results)
