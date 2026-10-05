"""E5 asymmetric prefixes.

`intfloat/multilingual-e5-large` is trained with "query: " on the question side
and "passage: " on the document side. The repo embeds both sides raw, so the
model runs outside its training distribution — and the cross-lingual case
(Spanish question, English wiki) is exactly where that asymmetry carries the
most weight. The measured baseline misses "¿Qué es un Procedure...?" entirely
while ranking "GeneXus Cognitive API - Translate procedure" first.

These pin the prefixing itself. Whether it actually improves retrieval is a
separate question, answered by scripts/eval_retrieval.py against the gold set —
not by this file.
"""

import numpy as np
import pytest

from src.vectorstore import embeddings as emb

pytestmark = pytest.mark.unit


class _RecordingModel:
    """Stand-in that records exactly what text reached the encoder."""

    def __init__(self):
        self.seen: list[str] = []

    def encode(self, texts, normalize_embeddings=False):
        self.seen.extend(texts)
        return np.zeros((len(texts), 3))


@pytest.fixture
def model(monkeypatch):
    recorder = _RecordingModel()
    monkeypatch.setattr(emb, "get_model", lambda: recorder)
    # embed_texts is lru_cached; a cached entry from another test would answer
    # without ever reaching the recorder.
    emb.embed_texts.cache_clear()
    yield recorder
    emb.embed_texts.cache_clear()


def test_a_query_is_prefixed_for_the_query_side(model):
    emb.embed_query("cómo se calcula la mora")

    assert model.seen == ["query: cómo se calcula la mora"]


def test_passages_are_prefixed_for_the_document_side(model):
    emb.embed_passages(("primer chunk", "segundo chunk"))

    assert model.seen == ["passage: primer chunk", "passage: segundo chunk"]


def test_an_already_prefixed_passage_is_not_prefixed_twice(model):
    # A partial re-embed will re-read text that already carries the prefix.
    # Double-prefixing is silent and would poison the vectors it was meant to fix.
    emb.embed_passages(("passage: ya viene",))

    assert model.seen == ["passage: ya viene"]


def test_an_already_prefixed_query_is_not_prefixed_twice(model):
    emb.embed_query("query: ya viene")

    assert model.seen == ["query: ya viene"]


def test_the_query_prefix_is_not_confused_with_the_passage_prefix(model):
    # "passage: x" asked as a query must still get the query prefix: the check
    # is per-side, not "does it start with any known prefix".
    emb.embed_query("passage: texto")

    assert model.seen == ["query: passage: texto"]


def test_embed_query_returns_a_single_vector(model):
    result = emb.embed_query("x")

    assert result.shape == (3,)


def test_the_raw_helpers_stay_unprefixed(model):
    # The existing collections were embedded raw. Until they are rebuilt, the
    # unprefixed path has to keep behaving exactly as before, or every stored
    # vector becomes unreachable.
    emb.embed("sin prefijo")

    assert model.seen == ["sin prefijo"]
