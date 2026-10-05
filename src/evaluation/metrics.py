"""Retrieval quality metrics — the layer this project was missing.

Everything else in the audit was downstream of one absence: no number said
whether retrieval works. Without it, `chunk_size`, the e5 prefixes, the
per-article cap and the RRF constant are all unverified hypotheses, and the
151 structural tests stay green through a total quality collapse.

Design constraints, both learned from what went wrong here:

- **Pure functions over rankings of ids.** No vector store, no model, no
  network. A metric that needs 20s of model load to run is a metric nobody
  runs, and an unrun metric is the same as no metric.
- **Article ids, not chunk ids.** A question is answered by a *document*; which
  of its chunks surfaced is an implementation detail that changes every time
  chunking is retuned. Scoring chunk ids would make the gold set expire on the
  next chunking change — exactly the regression it exists to catch.
"""

from dataclasses import dataclass, field


def recall_at_k(expected: set[str], ranked: list[str], k: int) -> float:
    """Fraction of `expected` ids appearing in the first `k` of `ranked`.

    Deduplicates: chunk-level rankings repeat an article id, and counting hits
    instead of distinct documents lets one article score above 1.0.

    Raises on an empty `expected` rather than returning 1.0 — an unanswerable
    case must not quietly inflate the suite average.
    """
    if not expected:
        raise ValueError("a case with no expected documents cannot be scored")
    found = expected & set(ranked[:k])
    return len(found) / len(expected)


def reciprocal_rank(expected: set[str], ranked: list[str]) -> float:
    """1/rank of the best-placed expected id (1-indexed), or 0.0 if absent.

    Rewards putting the answer first, which is what actually matters to a
    consumer that reads the top result and stops.
    """
    for index, doc_id in enumerate(ranked, start=1):
        if doc_id in expected:
            return 1.0 / index
    return 0.0


@dataclass(frozen=True)
class CaseResult:
    """One gold question scored against one ranking."""

    question: str
    expected: frozenset[str]
    recall: dict[int, float]
    mrr: float
    #: 1-indexed position of the best expected hit; None when it never appeared.
    rank: int | None

    @property
    def missed(self) -> bool:
        return self.rank is None


@dataclass(frozen=True)
class Report:
    """Suite-level summary. `misses` is the actionable part, not the averages."""

    total: int
    recall: dict[int, float]
    mrr: float
    misses: tuple[CaseResult, ...] = field(default_factory=tuple)


def evaluate_case(
    question: str,
    expected: set[str],
    ranked: list[str],
    ks: tuple[int, ...] = (5, 10),
) -> CaseResult:
    """Score one question. `ranked` is article ids, best first."""
    rr = reciprocal_rank(expected, ranked)
    return CaseResult(
        question=question,
        expected=frozenset(expected),
        recall={k: recall_at_k(expected, ranked, k) for k in ks},
        mrr=rr,
        rank=round(1 / rr) if rr else None,
    )


def summarize(cases: list[CaseResult]) -> Report:
    """Average the case results, keeping every miss for inspection.

    The averages tell you whether a change helped; the miss list tells you what
    to fix. Reporting only the average is how a suite becomes a dashboard nobody
    acts on.
    """
    if not cases:
        return Report(total=0, recall={}, mrr=0.0)

    ks = cases[0].recall.keys()
    return Report(
        total=len(cases),
        recall={k: sum(c.recall[k] for c in cases) / len(cases) for k in ks},
        mrr=sum(c.mrr for c in cases) / len(cases),
        misses=tuple(c for c in cases if c.missed),
    )
