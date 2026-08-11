"""
Retriever hibrido sobre PostgreSQL: denso (pgvector) + lexico (tsvector),
fusionados con Reciprocal Rank Fusion resuelto en una sola query SQL.

Reemplaza a retriever.py (ChromaDB + rank_bm25). La diferencia de fondo no es el
motor sino donde ocurre el trabajo: el BM25 anterior levantaba la coleccion
entera a memoria en paginas de 5000 y la cacheaba por proceso. Aca el indice
GIN vive en disco y lo mantiene el motor, asi que desaparecen _get_all(),
_bm25_cache y el paginado defensivo.

El contrato de hybrid_search() no cambia: mismos parametros, mismas claves.
"""

from __future__ import annotations

import logging
import re

from src.vectorstore import embeddings as emb
from src.vectorstore.pg_client import get_pool

log = logging.getLogger(__name__)

RRF_K = 60  # constante de amortiguacion estandar de RRF
CANDIDATE_POOL = 30  # candidatos por retriever antes de fusionar

# RRF resuelto en SQL. Cada CTE rankea por su criterio y la fusion suma
# 1/(K + rank), que es exactamente lo que hacia _reciprocal_rank_fusion() en
# Python — solo que sin traer los candidatos al proceso para ordenarlos.
_SEARCH_SQL = """
WITH dense AS (
    SELECT chunk_id,
           ROW_NUMBER() OVER (ORDER BY embedding <=> %(qvec)s::vector) AS rank
    FROM chunks
    WHERE collection = %(collection)s
    ORDER BY embedding <=> %(qvec)s::vector
    LIMIT %(pool)s
),
lexical AS (
    SELECT chunk_id,
           ROW_NUMBER() OVER (ORDER BY ts_rank_cd(tsv, q) DESC) AS rank
    -- es_simple, NO simple: tiene que ser LA MISMA config con la que se genero
    -- chunks.tsv (sql/002-unaccent-tsv.sql). Con 'simple' aca, una consulta CON
    -- tilde produce un token acentuado que ya no existe en el indice normalizado
    -- y devuelve CERO filas — medido: 'autorización' 97 -> 0.
    FROM chunks, to_tsquery('es_simple', %(qtext)s) AS q
    WHERE collection = %(collection)s AND tsv @@ q
    ORDER BY ts_rank_cd(tsv, q) DESC
    LIMIT %(pool)s
),
fused AS (
    SELECT chunk_id, SUM(1.0 / (%(rrf_k)s + rank)) AS score
    FROM (SELECT * FROM dense UNION ALL SELECT * FROM lexical) AS r
    GROUP BY chunk_id
)
SELECT c.chunk_id, c.article_id, c.title, c.content,
       c.source, c.url, c.content_hash, f.score
FROM fused f
JOIN chunks c USING (chunk_id)
WHERE %(min_score)s::float8 IS NULL OR f.score >= %(min_score)s::float8
ORDER BY f.score DESC
LIMIT %(top_k)s
"""


def _tokenize(text: str) -> list[str]:
    """Mismo criterio que el tokenizador del BM25 que se reemplaza."""
    return re.findall(r"\w+", text.lower())


def _to_tsquery(query: str) -> str:
    """Convierte texto libre en una tsquery con OR entre terminos.

    Se usa OR y no plainto_tsquery (que hace AND) porque BM25 puntua de forma
    aditiva: un documento que contiene 1 de 3 terminos igual entra al ranking.
    Con AND ese documento desapareceria y el recall caeria contra el baseline.
    """
    tokens = _tokenize(query)
    return " | ".join(tokens)


def hybrid_search(
    query: str,
    collection: str = "gx_docs",
    top_k: int = 5,
    min_score: float | None = None,
) -> list[dict]:
    """
    Busqueda hibrida: embeddings densos + full-text, fusionados con RRF.

    RRF es rank-based, asi que fusiona los dos retrievers sin tener que
    reconciliar escalas incompatibles (distancia coseno vs ts_rank_cd).

    min_score, si se pasa, descarta resultados por debajo de ese valor. OJO: el
    score de RRF es posicional y NO esta calibrado (no es una probabilidad);
    sirve como piso de ranking, no como umbral de confianza.
    """
    qtext = _to_tsquery(query)
    if not qtext:
        return []

    qvec = "[" + ",".join(f"{float(v):.7g}" for v in emb.embed(query)) + "]"

    params = {
        "qvec": qvec,
        "qtext": qtext,
        "collection": collection,
        "pool": CANDIDATE_POOL,
        "rrf_k": RRF_K,
        "top_k": top_k,
        "min_score": min_score,
    }

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(_SEARCH_SQL, params)
        rows = cur.fetchall()

    return [
        {
            # doc_id es el id del chunk — la clave que acepta get_document().
            "doc_id": row[0],
            # id es el id del articulo, para agrupar y citar procedencia.
            "id": row[1],
            "title": row[2],
            "content": row[3],
            "source": row[4],
            "url": row[5],
            "content_hash": row[6],
            "score": round(float(row[7]), 6),
        }
        for row in rows
    ]
