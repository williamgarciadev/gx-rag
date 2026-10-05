"""
Embeddings — sentence-transformers wrapper con caché.
"""

import logging
from functools import lru_cache
from sentence_transformers import SentenceTransformer
import numpy as np

log = logging.getLogger(__name__)

_model = None
EMBEDDING_MODEL = "intfloat/multilingual-e5-large"


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        log.info(f"Loading embedding model: {EMBEDDING_MODEL}")
        _model = SentenceTransformer(EMBEDDING_MODEL)
        log.info("Embedding model loaded")
    return _model


@lru_cache(maxsize=1024)
def embed_texts(texts: tuple[str, ...]) -> np.ndarray:
    """
    Embed one or more texts. Results are cached.
    texts must be a tuple (hashable) for lru_cache.
    """
    model = get_model()
    embeddings = model.encode(list(texts), normalize_embeddings=True)
    return embeddings


def embed(text: str) -> np.ndarray:
    """Embed a single text."""
    return embed_texts((text,))[0]


# ─── E5 asymmetric prefixes ───────────────────────────────────────────────────
#
# e5 is trained with a different prefix per side: the question carries
# "query: " and the document "passage: ". Embedding both raw runs the model
# outside its training distribution, and the cross-lingual case this corpus
# lives in (Spanish questions, English wiki) is where that costs the most.
#
# NOT wired into indexer/retriever yet: the 54k stored vectors are unprefixed,
# and mixing the two conventions in one collection is worse than either
# convention applied consistently. Rebuilding a collection is a ~5h decision
# gated on the gold-set A/B — see scripts/eval_retrieval.py.
E5_QUERY_PREFIX = "query: "
E5_PASSAGE_PREFIX = "passage: "


def _prefixed(texts: tuple[str, ...], prefix: str) -> tuple[str, ...]:
    """Apply `prefix` to each text that does not already carry it.

    The idempotency check matters during a partial re-embed, which re-reads
    text that may already be prefixed: double-prefixing is silent and would
    corrupt exactly the vectors the rebuild set out to fix.
    """
    return tuple(t if t.startswith(prefix) else f"{prefix}{t}" for t in texts)


def embed_query(text: str) -> np.ndarray:
    """Embed a search query, e5 query side."""
    return embed_texts(_prefixed((text,), E5_QUERY_PREFIX))[0]


def embed_passages(texts: tuple[str, ...]) -> np.ndarray:
    """Embed documents to be indexed, e5 passage side."""
    return embed_texts(_prefixed(tuple(texts), E5_PASSAGE_PREFIX))
