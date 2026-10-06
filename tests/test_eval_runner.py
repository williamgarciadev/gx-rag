"""Turning retriever hits into a scorable ranking.

The subtle part is not the metric, it is what counts as a rank. Retrieval works
at chunk granularity and the gold set names documents, so three chunks of one
manual chapter are ONE result — if they consumed three ranks, a single article
would fill the cutoff and Recall@5 would measure the cap, not the retriever.
"""

import pytest

from src.evaluation.gold import GoldCase
from src.evaluation.runner import ranked_article_ids, run_gold

pytestmark = pytest.mark.unit


def _case(question="q", collection="gx_docs", expected=("a",), filters=None):
    return GoldCase(
        question=question,
        collection=collection,
        expected=frozenset(expected),
        note="n",
        filters=filters,
    )


def _search(hits):
    """Fake retriever returning `hits`, recording how it was called."""
    calls = []

    def _fn(query, collection="gx_docs", top_k=5, filters=None):
        calls.append((query, collection, top_k, filters))
        return hits

    return _fn, calls


def test_chunks_of_one_article_collapse_to_a_single_rank():
    search, _ = _search(
        [
            {"doc_id": "cap_c0", "id": "cap"},
            {"doc_id": "cap_c1", "id": "cap"},
            {"doc_id": "otro_c0", "id": "otro"},
        ]
    )

    assert ranked_article_ids(_case(), top_k=10, search_fn=search) == ["cap", "otro"]


def test_rank_order_is_preserved():
    search, _ = _search([{"id": "b"}, {"id": "a"}, {"id": "c"}])

    assert ranked_article_ids(_case(), top_k=10, search_fn=search) == ["b", "a", "c"]


def test_a_hit_without_a_metadata_id_falls_back_to_its_chunk_id():
    # Wiki chunks always carry 'id'; a malformed one must still be rankable
    # rather than silently dropped, which would understate recall.
    search, _ = _search([{"doc_id": "x_c0", "id": ""}])

    assert ranked_article_ids(_case(), top_k=10, search_fn=search) == ["x_c0"]


def test_the_case_collection_and_depth_reach_the_retriever():
    search, calls = _search([])

    ranked_article_ids(_case(collection="bt_docs"), top_k=20, search_fn=search)

    assert calls == [("q", "bt_docs", 20, None)]


def test_the_case_filters_reach_the_retriever():
    # En gx_docs los tickets SAC son más de la mitad del corpus y la tool le pide
    # al agente filtrar origin=gx_wiki. Sin pasar el filtro, el set gold medía una
    # búsqueda que ningún agente bien instruido hace: 2 de 9 casos encontrados
    # sin filtro, 8 de 9 con él (medido 2026-10-06).
    search, calls = _search([])

    ranked_article_ids(_case(filters={"origin": "gx_wiki"}), top_k=20, search_fn=search)

    assert calls == [("q", "gx_docs", 20, {"origin": "gx_wiki"})]


def test_run_gold_scores_every_case():
    search, _ = _search([{"id": "a"}])
    cases = [_case(question="q1", expected=("a",)), _case(question="q2", expected=("z",))]

    results, report = run_gold(cases, top_k=10, ks=(5,), search_fn=search)

    assert [r.rank for r in results] == [1, None]
    assert report.recall == {5: 0.5}
    assert [c.question for c in report.misses] == ["q2"]
