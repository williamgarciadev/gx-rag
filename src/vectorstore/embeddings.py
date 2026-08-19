"""
Embeddings — sentence-transformers wrapper con caché y PERFIL de geometría.

QUE ES UN PERFIL. `intfloat/multilingual-e5-large` fue entrenado con prefijos de rol:
`query: ` en la consulta y `passage: ` en el documento. Este código nunca los agregó, y
eso NO estaba roto: el corpus quedó simétricamente sin prefijo, o sea internamente
consistente. Está en una geometría distinta de la que el modelo entrenó, que es peor
que la ideal pero no incoherente.

POR QUE UN PERFIL Y NO UN CAMBIO DIRECTO. Prefijar solo la query contra un corpus sin
prefijar es un DESAJUSTE, y un desajuste es peor que la ausencia consistente: arreglar
la mitad es activamente dañino. Entonces el prefijo no se puede "activar": hay que
re-embeddear el corpus entero de la colección, y para saber qué está en qué geometría
hace falta marcarlo (columna `emb_profile`, sql/005).

  raw    sin prefijos. Es lo que hay indexado hoy y sigue siendo el DEFAULT.
  e5v1   con `query: ` / `passage: `, como el modelo espera.

Se elige con la env var `GXRAG_EMB_PROFILE`. El default `raw` produce vectores
byte-idénticos a los de antes de este cambio: mientras nadie lo toque, esto es un
no-op.
"""

import logging
import os
from functools import lru_cache

import numpy as np
from sentence_transformers import SentenceTransformer

log = logging.getLogger(__name__)

_model = None
EMBEDDING_MODEL = "intfloat/multilingual-e5-large"

PERFILES = ("raw", "e5v1")
EMB_PROFILE = os.environ.get("GXRAG_EMB_PROFILE", "raw").strip().lower()
if EMB_PROFILE not in PERFILES:
    raise ValueError(
        f"GXRAG_EMB_PROFILE={EMB_PROFILE!r} no reconocido. Validos: {', '.join(PERFILES)}. "
        "Un perfil mal escrito silenciaria el prefijo y dejaria el corpus en una "
        "geometria distinta de la que dice la columna emb_profile."
    )


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        log.info(f"Loading embedding model: {EMBEDDING_MODEL} (profile={EMB_PROFILE})")
        _model = SentenceTransformer(EMBEDDING_MODEL)
        log.info("Embedding model loaded")
    return _model


def _con_prefijo(rol: str, texto: str) -> str:
    """Aplica el prefijo del rol si el perfil lo pide.

    Se prefija ANTES de llamar a embed_texts, no adentro. Es deliberado: embed_texts
    tiene lru_cache, así que si el prefijo se aplicara dentro de la función el texto
    cacheado sería el mismo para los dos perfiles y un proceso caliente serviría
    geometría vieja en silencio tras cambiar el perfil. Prefijando antes, el prefijo
    entra a la CLAVE del caché.
    """
    return f"{rol}: {texto}" if EMB_PROFILE == "e5v1" else texto


@lru_cache(maxsize=1024)
def embed_texts(texts: tuple[str, ...]) -> np.ndarray:
    """
    Embed one or more texts. Results are cached.
    texts must be a tuple (hashable) for lru_cache.

    NIVEL BAJO: no aplica prefijos. Quien indexa o consulta debe usar embed_passage /
    embed_query, que son los que conocen el rol del texto. Se mantiene público porque
    scripts y tests lo usan para embeddear texto cuyo rol no aplica.
    """
    model = get_model()
    embeddings = model.encode(list(texts), normalize_embeddings=True)
    return embeddings


def embed(text: str) -> np.ndarray:
    """Embed a single text, sin prefijo. Ver embed_query / embed_passage."""
    return embed_texts((text,))[0]


def embed_query(text: str) -> np.ndarray:
    """Embed de una CONSULTA. Es lo que debe usar el retriever."""
    return embed_texts((_con_prefijo("query", text),))[0]


def embed_passage(texts: tuple[str, ...]) -> np.ndarray:
    """Embed de DOCUMENTOS. Es lo que debe usar el indexador."""
    return embed_texts(tuple(_con_prefijo("passage", t) for t in texts))
