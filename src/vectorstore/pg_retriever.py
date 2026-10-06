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

# Busqueda vectorial FILTRADA: sin esto la mitad densa devuelve CERO filas.
#
# El HNSW hace POST-filtrado: recorre el grafo, entrega del orden de hnsw.ef_search
# (default 40) tuplas y el ejecutor aplica `WHERE collection = ...` DESPUES. Si esas
# 40 pertenecen a otra coleccion, quedan cero. El grafo NO se reintenta.
#
# Medido sobre el corpus real (51.211 chunks; gx_docs 50.063 / bt_docs 1.122 /
# gx_syntax 26), con la query 'saldo contable de la cuenta':
#
#     top-40 vecinos SIN filtro          -> 40/40 son de bt_docs
#     filtrando gx_docs, ef_search=40    ->  0 filas densas
#     filtrando gx_docs, ef_search=1000  -> 30 filas
#     filtrando gx_docs, iterative_scan  -> 30 filas
#
# La falla era SILENCIOSA: sin error, y sin cero resultados totales, porque la mitad
# lexica tapaba el hueco y el RRF degeneraba a lexico puro. El sintoma observable es
# un score maximo de exactamente 1/(60+1) = 0.016393 -> todo entro por UNA sola
# mitad en rank 1. Medido en 3 de 4 consultas de prueba, incluida
# 'work with plus grid filters' sobre gx_docs, que no es ningun caso de borde.
#
# relaxed_order y no strict_order: el pool de CANDIDATE_POOL alimenta una fusion por
# RANGO (ROW_NUMBER), no por valor, asi que un orden aproximado de distancia no
# cambia el RRF. strict_order es la alternativa conservadora si alguna medicion
# mostrara degradacion de ranking.
#
# Requiere pgvector >= 0.8 (instalado: 0.8.6).
_TUNING_SQL = "SET LOCAL hnsw.iterative_scan = 'relaxed_order'"

# Claves de filtro DECLARADAS (metadatos de sql/003-chunk-meta.sql). Cerrada a
# proposito: es lo que permite que el SQL siga siendo una constante de modulo en vez
# de texto armado en runtime, y que _reject_unknown_filter pueda RECHAZAR en vez de
# ignorar una clave desconocida.
FILTER_KEYS = (
    "kb_name",
    "obj_type",
    "obj_name",
    "chunk_kind",
    "module",
    "origin",
    "related",
)

# El bloque de filtros se define UNA vez y se interpola en LOS DOS CTE.
#
# POR QUE DENTRO DE CADA CTE Y NO DESPUES DE LA FUSION: cada mitad trae solo
# CANDIDATE_POOL candidatos y recien ahi se fusiona. Filtrando despues, esos
# candidatos se llenan de chunks que el filtro va a descartar: con una selectividad
# del 7% (224 Transactions sobre 3.157 objetos), de 30 candidatos genericos
# sobreviven 0 o 1, y la busqueda FILTRADA devuelve peor que la sin filtrar. El
# agente lee "casi no hay transacciones sobre esto", que es una afirmacion sobre el
# corpus derivada de un artefacto del pipeline.
#
# Escribirlo dos veces a mano seria la forma de que las dos mitades filtren distinto,
# y ese bug no se ve: la busqueda devuelve resultados, solo que la lexica trae chunks
# que la densa ya habia descartado. De ahi que sea una sola constante interpolada.
#
# Cada predicado es un NO-OP cuando su parametro es NULL: eso es lo que permite que
# la SQL sea constante, sin nada que concatenar segun que filtros vinieron.
_META_FILTER = """
      AND (%(f_kb_name)s::text[]    IS NULL OR meta->>'kb_name'    = ANY(%(f_kb_name)s::text[]))
      AND (%(f_obj_type)s::text[]   IS NULL OR meta->>'obj_type'   = ANY(%(f_obj_type)s::text[]))
      AND (%(f_obj_name)s::text[]   IS NULL OR meta->>'obj_name'   = ANY(%(f_obj_name)s::text[]))
      AND (%(f_chunk_kind)s::text[] IS NULL OR meta->>'chunk_kind' = ANY(%(f_chunk_kind)s::text[]))
      AND (%(f_module)s::text[]     IS NULL OR meta->>'module'     = ANY(%(f_module)s::text[]))
      AND (%(f_origin)s::text[]     IS NULL OR meta->>'origin'     = ANY(%(f_origin)s::text[]))
      AND (%(f_related)s::text[]    IS NULL OR meta->'related' ?| %(f_related)s::text[])
"""

# RRF resuelto en SQL. Cada CTE rankea por su criterio y la fusion suma
# 1/(K + rank), que es exactamente lo que hacia _reciprocal_rank_fusion() en
# Python — solo que sin traer los candidatos al proceso para ordenarlos.
_SEARCH_SQL = f"""
WITH dense AS (
    SELECT chunk_id,
           ROW_NUMBER() OVER (ORDER BY embedding <=> %(qvec)s::vector) AS rank
    FROM chunks
    WHERE collection = %(collection)s{_META_FILTER}
    ORDER BY embedding <=> %(qvec)s::vector
    LIMIT %(pool)s
),
lexical AS (
    SELECT chunk_id,
           ROW_NUMBER() OVER (ORDER BY ts_rank_cd(tsv, q) DESC) AS rank
    -- es_simple, NO simple: tiene que ser LA MISMA config con la que se genero
    -- chunks.tsv (sql/001, y sql/002 para las bases creadas antes). Con 'simple'
    -- aca, una consulta CON tilde produce un token acentuado que ya no existe en el
    -- indice normalizado y devuelve CERO filas — medido: 'autorización' 97 -> 0.
    FROM chunks, to_tsquery('es_simple', %(qtext)s) AS q
    WHERE collection = %(collection)s AND tsv @@ q{_META_FILTER}
    ORDER BY ts_rank_cd(tsv, q) DESC
    LIMIT %(pool)s
),
fused AS (
    SELECT chunk_id, SUM(1.0 / (%(rrf_k)s + rank)) AS score
    FROM (SELECT * FROM dense UNION ALL SELECT * FROM lexical) AS r
    GROUP BY chunk_id
),
ranked_articles AS (
    SELECT c.chunk_id, c.article_id, c.title, c.content,
           c.source, c.url, c.content_hash, c.meta, f.score,
           ROW_NUMBER() OVER (PARTITION BY c.article_id ORDER BY f.score DESC) AS article_rank
    FROM fused f
    JOIN chunks c USING (chunk_id)
    WHERE %(min_score)s::float8 IS NULL OR f.score >= %(min_score)s::float8
)
SELECT chunk_id, article_id, title, content,
       source, url, content_hash, meta, score
FROM ranked_articles
WHERE %(max_per_article)s::int IS NULL OR %(max_per_article)s::int <= 0 OR article_rank <= %(max_per_article)s::int
ORDER BY score DESC
LIMIT %(top_k)s
"""


def filter_params(filters: dict | None) -> dict:
    """Un parametro por clave DECLARADA, o None. Publica: la usa bench_retrieval.

    RECHAZA una clave desconocida, no la ignora. Ignorarla devolveria resultados SIN
    FILTRAR que el llamador cree filtrados — la misma mentira de ruteo silenciosa que
    tuvo add_from_url cuando ignoraba `collection`. El mensaje nombra las validas.

    Una lista VACIA tambien se rechaza: en SQL `= ANY('{}')` es FALSE para toda fila,
    o sea "Sin resultados", que el agente lee como evidencia sobre el CORPUS y no
    sobre su propio argumento.
    """
    filters = filters or {}
    desconocidas = sorted(set(filters) - set(FILTER_KEYS))
    if desconocidas:
        raise ValueError(
            f"Filtro(s) desconocido(s): {', '.join(desconocidas)}. "
            f"Validos: {', '.join(FILTER_KEYS)}. No se consulto el store."
        )

    params: dict = {}
    for clave in FILTER_KEYS:
        crudo = filters.get(clave)
        if crudo is None:
            params[f"f_{clave}"] = None
            continue
        valores = [crudo] if isinstance(crudo, str) else [v for v in crudo if v]
        if not valores:
            raise ValueError(
                f"Filtro '{clave}' vino vacio. Omitilo para no filtrar por esa clave."
            )
        params[f"f_{clave}"] = valores
    return params


def _tokenize(text: str) -> list[str]:
    """Tokeniza en palabras alfanuméricas, excluyendo '_' para que to_tsquery no
    convierta identificadores con guión bajo (foo_bar) al operador de frase estricta <->."""
    return re.findall(r"[^\W_]+", text.lower())


def _to_tsquery(query: str) -> str:
    """Convierte texto libre en una tsquery con OR entre terminos.

    Se usa OR y no plainto_tsquery (que hace AND) porque BM25 puntua de forma
    aditiva: un documento que contiene 1 de 3 terminos igual entra al ranking.
    Con AND ese documento desapareceria y el recall caeria contra el baseline.
    """
    tokens = _tokenize(query)
    return " | ".join(tokens)


# Las tres piezas de una búsqueda, públicas para que scripts/bench_retrieval.py
# mida EXACTAMENTE lo que corre acá en vez de reimplementarlo: el bench copiaba
# el SQL y divergió en tres puntos (sin SET LOCAL, sin prepare=False, sin el
# perfil de embedding), así que comparaba planes distintos. Van en este orden a
# propósito: los filtros se validan ANTES de embeber, y un argumento rechazado
# no paga la carga del modelo (~2 GB).


def search_params(
    query: str,
    collection: str,
    top_k: int,
    *,
    min_score: float | None = None,
    filters: dict | None = None,
    max_per_article: int | None = None,
) -> dict | None:
    """Parámetros del SQL de búsqueda, sin el vector. None si la consulta no deja
    ningún término para la mitad léxica. Valida `filters` (ValueError)."""
    qtext = _to_tsquery(query)
    if not qtext:
        return None
    multiplier = 4 if (max_per_article and max_per_article > 0) else 3
    return {
        "qtext": qtext,
        "collection": collection,
        # El pool acompaña a top_k: con el fijo en CANDIDATE_POOL, un top_k > 30
        # quedaba capado en silencio por el LIMIT de cada CTE. Con max_per_article
        # activo, usamos x4 para garantizar diversidad de candidatos antes del corte.
        "pool": max(CANDIDATE_POOL, top_k * multiplier),
        "rrf_k": RRF_K,
        "top_k": top_k,
        "min_score": min_score,
        "max_per_article": max_per_article,
        **filter_params(filters),
    }


def query_vector(query: str) -> str:
    """Literal pgvector de la consulta, con el perfil de embedding (embed_query)."""
    return "[" + ",".join(f"{float(v):.7g}" for v in emb.embed_query(query)) + "]"


def execute_search(params: dict, *, explain: bool = False) -> list[tuple]:
    """Ejecuta _SEARCH_SQL como producción: dentro de una transacción con el
    ajuste de HNSW, y sin preparar la sentencia.

    explain=True devuelve las líneas de EXPLAIN (ANALYZE, BUFFERS) del MISMO
    camino: un plan sacado fuera de esta transacción describe uno que producción
    nunca ejecuta."""
    sql = "EXPLAIN (ANALYZE, BUFFERS) " + _SEARCH_SQL if explain else _SEARCH_SQL
    with get_pool().connection() as conn:
        # SET LOCAL exige un bloque de transaccion, y conn.transaction() lo ACOTA:
        # una conexion que se devolviera al pool con iterative_scan pegado se lo
        # llevaria al siguiente que la tome, cambiando el plan de consultas ajenas.
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(_TUNING_SQL)
            # prepare=False: a la 5a ejecucion psycopg3 auto-prepara la sentencia y el
            # plan puede volverse GENERICO. Con plan generico los parametros son
            # incognitas y las guardas `%(f_x)s::text[] IS NULL OR ...` ya no se
            # colapsan en tiempo de planificacion: se pierden los indices
            # chunks_meta_* y el CTE lexico pasa a seq scan. El acantilado aparece en
            # la 6a consulta, no en la 1a, que es la peor forma de descubrirlo.
            cur.execute(sql, params, prepare=False)
            return cur.fetchall()


def hybrid_search(
    query: str,
    collection: str = "gx_docs",
    top_k: int = 5,
    min_score: float | None = None,
    filters: dict | None = None,
    rerank: bool = False,
    max_per_article: int | None = None,
) -> list[dict]:
    """
    Busqueda hibrida: embeddings densos + full-text, fusionados con RRF.

    RRF es rank-based, asi que fusiona los dos retrievers sin tener que
    reconciliar escalas incompatibles (distancia coseno vs ts_rank_cd).

    min_score, si se pasa, descarta resultados por debajo de ese valor. OJO: el
    score de RRF es posicional y NO esta calibrado (no es una probabilidad);
    sirve como piso de ranking, no como umbral de confianza. Y un score de 1/(K+1)
    NO prueba que el tema falte: solo dice que ese chunk entro por una sola mitad.

    filters: {clave: valor | [valores]} sobre los metadatos de chunks.meta, con las
    claves de FILTER_KEYS. Se aplican DENTRO de cada mitad del RRF (ver _META_FILTER).
    Matiz de calibracion: un filtro selectivo hace que cada mitad devuelva menos
    candidatos, los ranks quedan mejores y los scores SUBEN en promedio, asi que un
    min_score calibrado sin filtros no es directamente transferible.

    rerank: si es True, reordena los mejores candidatos de la fusion RRF usando un
    Cross-Encoder de atencion completa (sentence-transformers), agregando rerank_score
    y conservando rrf_score.

    max_per_article: si se define (> 0), limita la cantidad de fragmentos por
    artículo en el resultado final, promoviendo diversidad de fuentes.
    """
    # Si se pide rerank, ampliamos la cantidad de candidatos recuperados de la DB
    db_top_k = max(CANDIDATE_POOL, top_k * 4) if rerank else top_k

    params = search_params(
        query,
        collection,
        db_top_k,
        min_score=min_score,
        filters=filters,
        max_per_article=max_per_article,
    )
    if params is None:
        return []
    params["qvec"] = query_vector(query)
    rows = execute_search(params)

    results = [
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
            # meta viaja al llamador para que pueda CITAR el objeto (obj_name,
            # obj_type) en vez de solo el chunk. Vacio {} en las colecciones de
            # documentacion, asi que el consumidor no necesita ramificar por
            # coleccion: o hay metadatos o el dict esta vacio.
            "meta": row[7] or {},
            "score": round(float(row[8]), 6),
        }
        for row in rows
    ]

    if rerank and results:
        from src.vectorstore.reranker import rerank as _rerank_candidates

        return _rerank_candidates(query=query, candidates=results, top_k=top_k)

    return results
