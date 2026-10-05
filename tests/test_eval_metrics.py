"""Retrieval metrics.

The whole audit hinged on one absence: nothing here measured whether retrieval
works. Chunking was redesigned page->chapter and 151 tests stayed green, because
they check structure, not quality. These are the numbers that would have caught
it — and the first real question we tried ("what is the API object?") already
scores Recall@5 = 0.

Pure functions on purpose: the metric must be checkable without a vector store,
or it becomes another thing nobody runs.
"""

import pytest

from src.evaluation.metrics import (
    evaluate_case,
    recall_at_k,
    reciprocal_rank,
    summarize,
)

pytestmark = pytest.mark.unit


# ─── recall_at_k ──────────────────────────────────────────────────────────────


def test_recall_is_one_when_the_expected_doc_is_in_the_top_k():
    assert recall_at_k({"a"}, ["x", "a", "y"], k=3) == 1.0


def test_recall_is_zero_when_the_expected_doc_is_below_the_cutoff():
    # 'a' is retrieved, but at rank 4 — outside k=3. Retrieved-but-buried is a
    # miss: nothing downstream reads past the cutoff.
    assert recall_at_k({"a"}, ["x", "y", "z", "a"], k=3) == 0.0


def test_recall_counts_the_fraction_of_expected_docs_found():
    # Two docs expected, one found inside k -> 0.5. A question whose answer is
    # split across two chapters is only half answered by one of them.
    assert recall_at_k({"a", "b"}, ["a", "x", "y"], k=3) == 0.5


def test_recall_ignores_duplicate_hits_of_the_same_doc():
    # Chunk-level rankings repeat an article id; counting each hit would let one
    # article manufacture recall above 1.0.
    assert recall_at_k({"a"}, ["a", "a", "a"], k=3) == 1.0


def test_recall_of_an_empty_ranking_is_zero():
    assert recall_at_k({"a"}, [], k=5) == 0.0


def test_recall_with_no_expected_docs_is_undefined_not_perfect():
    # A case with no ground truth cannot be scored; silently returning 1.0 would
    # inflate the suite average with unanswerable questions.
    with pytest.raises(ValueError):
        recall_at_k(set(), ["a"], k=5)


# ─── reciprocal_rank ──────────────────────────────────────────────────────────


def test_reciprocal_rank_is_one_for_a_first_place_hit():
    assert reciprocal_rank({"a"}, ["a", "b"]) == 1.0


def test_reciprocal_rank_uses_the_best_ranked_expected_doc():
    # Rank 3 (1-indexed) -> 1/3.
    assert reciprocal_rank({"a"}, ["x", "y", "a"]) == pytest.approx(1 / 3)


def test_reciprocal_rank_is_zero_when_nothing_expected_was_retrieved():
    assert reciprocal_rank({"a"}, ["x", "y"]) == 0.0


# ─── evaluate_case ────────────────────────────────────────────────────────────


def test_evaluate_case_reports_the_rank_the_answer_landed_at():
    result = evaluate_case(
        question="q", expected={"a"}, ranked=["x", "a"], ks=(1, 5)
    )

    assert result.recall == {1: 0.0, 5: 1.0}
    assert result.rank == 2
    assert result.mrr == 0.5


def test_evaluate_case_reports_a_miss_with_no_rank():
    result = evaluate_case(question="q", expected={"a"}, ranked=["x"], ks=(5,))

    assert result.recall == {5: 0.0}
    assert result.rank is None
    assert result.missed is True


# ─── summarize ────────────────────────────────────────────────────────────────


def test_summarize_averages_recall_across_cases():
    cases = [
        evaluate_case("q1", {"a"}, ["a"], ks=(5,)),
        evaluate_case("q2", {"b"}, ["x"], ks=(5,)),
    ]

    report = summarize(cases)

    assert report.recall == {5: 0.5}
    assert report.mrr == 0.5
    assert report.total == 2
    assert [c.question for c in report.misses] == ["q2"]


def test_summarize_of_no_cases_does_not_divide_by_zero():
    report = summarize([])

    assert report.total == 0
    assert report.mrr == 0.0
