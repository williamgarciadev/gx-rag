#!/usr/bin/env python3
"""Baseline de latencia del retrieval — Fase 0 de `docs/PLAN-optimizacion.md`.

El repo no tiene UNA medición de latencia en el camino de servicio
(`rg "perf_counter|EXPLAIN"` sobre `src/` sólo encuentra las ventanas del
rate-limit), así que ninguna optimización se puede justificar ni verificar. Esto
produce el número contra el que se compara todo lo que venga después.

DECISIÓN DE DISEÑO — se cronometran las dos mitades POR SEPARADO:

    embedding (e5-large, CPU)  +  SQL (pgvector + tsvector + RRF)

Sin esa separación no se sabe cuál domina, y la Fase 2 optimizaría a ciegas: si el
embedding es el 90% del tiempo, reescribir los CTE del SQL no mueve el total y el
trabajo se desperdicia. Llama a las MISMAS funciones que hybrid_search
(`search_params`, `query_vector`, `execute_search` de pg_retriever) en vez de
ejecutar el SQL por su cuenta: la copia anterior corría sin SET LOCAL, sin
prepare=False y sin el perfil de embedding, y medía un plan que producción no usa.

    .venv/Scripts/python.exe scripts/bench_retrieval.py
    .venv/Scripts/python.exe scripts/bench_retrieval.py --explain
    .venv/Scripts/python.exe scripts/bench_retrieval.py --repeat 5 --collection bt_docs
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dotenv

dotenv.load_dotenv(".env", override=False)

from src.vectorstore import embeddings as emb  # noqa: E402
from src.vectorstore.pg_client import get_pool  # noqa: E402
# Por el MÓDULO y no por nombre: el bench mide las funciones de producción, y así
# un cambio en pg_retriever le llega sin tocar este script.
from src.vectorstore import pg_retriever  # noqa: E402

# Consultas fijas: el baseline tiene que ser comparable entre corridas, así que no
# se sortean. Mezcla deliberada de una sola palabra, varias palabras e
# identificador exacto, porque el costo del CTE léxico depende de cuántas filas
# matchea la tsquery (ver hallazgo #40).
QUERIES: dict[str, list[str]] = {
    "gx_docs": [
        "for each command",
        "business component insert",
        "data provider",
        "GAM authentication",
        "web panel grid refresh",
    ],
    "bt_docs": [
        "cadena de cierre",
        "autorización de excepciones",
        "procesos batch",
        "plan de cuentas",
        "FSD011",
    ],
    "gx_syntax": ["AddYears method", "Default rule"],
}


def _percentiles(muestras: list[float]) -> tuple[float, float, float]:
    """p50/p95/p99 en ms. Con pocas muestras, p95 y p99 tienden al máximo: es
    honesto que se parezcan, no un error de cálculo."""
    ordenadas = sorted(muestras)
    n = len(ordenadas)

    def pct(p: float) -> float:
        if n == 1:
            return ordenadas[0]
        i = min(n - 1, max(0, int(round(p * (n - 1)))))
        return ordenadas[i]

    return pct(0.50), pct(0.95), pct(0.99)


def entorno() -> None:
    """Lo que hay que registrar para que el baseline sea interpretable después."""
    print("== Entorno ==")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("select version()")
        print(f"  postgres        : {cur.fetchone()[0].split(',')[0]}")
        cur.execute("select extversion from pg_extension where extname='vector'")
        row = cur.fetchone()
        print(f"  pgvector        : {row[0] if row else 'NO INSTALADA'}")
        # El repo no pinea pgvector en ningún lado, así que hasta ahora no se sabía
        # contra qué versión se corre.
        #
        # `hnsw.ef_search` es un GUC que pgvector registra al CARGARSE en la sesión,
        # no al instalar la extensión: consultarlo antes de tocar el índice devuelve
        # "no disponible" y hace pensar que la config no existe. Se fuerza la carga
        # con una consulta trivial que use el operador.
        try:
            cur.execute("select id from (select 1 as id) t where 1=0")
            cur.execute("select '[1,0]'::vector <=> '[0,1]'::vector")
            cur.fetchone()
        except Exception:
            conn.rollback()
        for parametro in ("hnsw.ef_search", "work_mem", "shared_buffers", "max_parallel_workers_per_gather"):
            try:
                cur.execute(f"show {parametro}")
                print(f"  {parametro:<32}: {cur.fetchone()[0]}")
            except Exception:
                conn.rollback()
                print(f"  {parametro:<32}: (no disponible)")
        cur.execute(
            "select collection, count(*) from chunks group by 1 order by 2 desc"
        )
        filas = cur.fetchall()
    print(f"  corpus          : {' · '.join(f'{c} {n}' for c, n in filas)}")
    print(f"  RRF_K={pg_retriever.RRF_K}  CANDIDATE_POOL={pg_retriever.CANDIDATE_POOL}  "
          f"modelo={emb.EMBEDDING_MODEL}  perfil={emb.EMB_PROFILE}")


def medir(collection: str, queries: list[str], repeat: int, top_k: int) -> None:
    print(f"\n== {collection} — {len(queries)} consultas × {repeat} repeticiones ==")
    print(f"  {'consulta':<34} {'embed p50':>10} {'sql p50':>9} {'sql p95':>9} {'total p50':>10}")

    todos_embed: list[float] = []
    todos_sql: list[float] = []

    for q in queries:
        t_embed: list[float] = []
        t_sql: list[float] = []
        # Sin filtros: el baseline mide la búsqueda SIN filtrar. Se arma fuera del
        # cronómetro porque es lo mismo en cada repetición y no cuesta nada medible.
        params = pg_retriever.search_params(q, collection, top_k)
        for _ in range(repeat):
            # cache_clear() antes de cronometrar: `embed_texts` tiene lru_cache, así
            # que sin esto la 2ª repetición mide un HIT de caché (~0,6 ms) en vez del
            # costo real del modelo (~115 ms) y el baseline queda inservible.
            # El caso "consulta repetida" es real y bueno, pero el que hay que medir
            # es la consulta NUEVA, que es lo que paga un agente cada vez.
            emb.embed_texts.cache_clear()
            ini = time.perf_counter()
            qvec = pg_retriever.query_vector(q)
            medio = time.perf_counter()
            pg_retriever.execute_search({**params, "qvec": qvec})
            fin = time.perf_counter()
            t_embed.append((medio - ini) * 1000)
            t_sql.append((fin - medio) * 1000)

        e50, _, _ = _percentiles(t_embed)
        s50, s95, _ = _percentiles(t_sql)
        print(f"  {q[:34]:<34} {e50:>9.1f}ms {s50:>8.1f}ms {s95:>8.1f}ms {e50 + s50:>9.1f}ms")
        todos_embed += t_embed
        todos_sql += t_sql

    e50, e95, e99 = _percentiles(todos_embed)
    s50, s95, s99 = _percentiles(todos_sql)
    tot = [a + b for a, b in zip(todos_embed, todos_sql)]
    t50, t95, t99 = _percentiles(tot)
    print(f"  {'—' * 76}")
    print(f"  embedding   p50 {e50:7.1f}ms   p95 {e95:7.1f}ms   p99 {e99:7.1f}ms")
    print(f"  SQL         p50 {s50:7.1f}ms   p95 {s95:7.1f}ms   p99 {s99:7.1f}ms")
    print(f"  TOTAL       p50 {t50:7.1f}ms   p95 {t95:7.1f}ms   p99 {t99:7.1f}ms")
    if t50:
        print(f"  reparto del p50: embedding {100 * e50 / t50:.0f}%  ·  SQL {100 * s50 / t50:.0f}%")


def explain(collection: str, query: str, top_k: int) -> None:
    """EXPLAIN (ANALYZE, BUFFERS) del SQL real, para el hallazgo #1."""
    print(f"\n== EXPLAIN ANALYZE — {collection} — {query!r} ==")
    params = {**pg_retriever.search_params(query, collection, top_k),
              "qvec": pg_retriever.query_vector(query)}
    for (linea,) in pg_retriever.execute_search(params, explain=True):
        # El `Order By` del Index Scan trae el vector de consulta ENTERO: 1024
        # floats que ocupan ~14 KB y hacen ilegible justo la línea que importa
        # (la que dice si se usó el índice). Se recorta.
        if "::vector" in linea and len(linea) > 200:
            ini = linea.index("'[")
            fin = linea.index("]'") + 2
            linea = linea[:ini] + "'[…1024 dims…]'" + linea[fin:]
        print(f"  {linea}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--repeat", type=int, default=3, help="repeticiones por consulta")
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--collection", help="medir sólo esta colección")
    p.add_argument("--explain", action="store_true", help="volcar EXPLAIN ANALYZE")
    args = p.parse_args(argv)

    entorno()

    # Warm-up FUERA de la medición: la primera llamada carga e5-large (~2.2 GB) y
    # contarla mezclaría el arranque en frío con la latencia de servicio. El costo
    # del arranque en frío es un hallazgo aparte (#8), no parte del baseline.
    ini = time.perf_counter()
    pg_retriever.query_vector("warm up")
    print(f"\n  (warm-up del modelo: {(time.perf_counter() - ini) * 1000:.0f} ms — NO cuenta)")

    objetivo = {args.collection: QUERIES[args.collection]} if args.collection else QUERIES
    for collection, queries in objetivo.items():
        medir(collection, queries, args.repeat, args.top_k)

    if args.explain:
        # gx_docs (50k) contra gx_syntax (26): el contraste expone qué hace el
        # filtro por colección sobre el HNSW.
        explain("gx_docs", "for each command", args.top_k)
        explain("gx_syntax", "AddYears method", args.top_k)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
