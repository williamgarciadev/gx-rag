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
