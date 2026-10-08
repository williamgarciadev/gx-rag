"""La búsqueda densa de cada colección recorre un HNSW que SOLO contiene esa colección.

EL BUG: había un único HNSW sobre toda la tabla `chunks`, y el filtro por colección
se aplicaba DESPUÉS de recorrer el grafo (post-filtrado, ver pg_retriever.py). Una
colección que crecía le cambiaba a las demás qué candidatos densos encontraban.
Medido el 2026-10-08, después de ingerir 21.665 chunks en bt_docs, sobre los 12
casos de eval/gold.jsonl con el mismo código:

    HNSW global          R@5 0,583  R@10 0,750  MRR 0,399
    búsqueda exacta      R@5 0,833  R@10 0,833  MRR 0,443

Y con el código del 2026-10-06, gx_docs devolvió OTRO ranking sin que gx_docs
hubiera cambiado: la ingesta de bt_docs le movió el grafo.

QUÉ PRUEBA: la propiedad estructural, por catálogo. Cada colección de
KNOWN_COLLECTIONS tiene un HNSW cuyo predicado es ella misma, y no queda ningún HNSW
SIN predicado: si quedara, el planner podría seguir eligiendo el grafo compartido.
Una colección nueva sin su índice parcial cae acá.

QUÉ **NO** PRUEBA: que el planner elija el índice parcial. Con la tabla del schema de
prueba vacía elige un btree y un sort (medido: `chunks_emb_profile`), así que un
EXPLAIN dependería del costo estimado y no del DDL. La elección real se midió sobre el
corpus (ver sql/006-hnsw-per-collection.sql).
"""

from __future__ import annotations

import pytest

from src.server import KNOWN_COLLECTIONS

pytestmark = pytest.mark.integration


@pytest.fixture
def hnsw_de_chunks(pg_dsn, pg_schema) -> dict[str, str]:
    """{nombre del índice HNSW sobre chunks: su predicado, '' si no tiene}."""
    import psycopg

    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT c.relname, coalesce(pg_get_expr(i.indpred, i.indrelid), '')
                 FROM pg_index i
                 JOIN pg_class c  ON c.oid = i.indexrelid
                 JOIN pg_class t  ON t.oid = i.indrelid
                 JOIN pg_namespace n ON n.oid = t.relnamespace
                 JOIN pg_am am    ON am.oid = c.relam
                WHERE n.nspname = %s AND t.relname = 'chunks' AND am.amname = 'hnsw'""",
            (pg_schema,),
        )
        return dict(cur.fetchall())


@pytest.mark.parametrize("collection", KNOWN_COLLECTIONS)
def test_each_collection_has_an_hnsw_of_its_own(hnsw_de_chunks, collection):
    propios = [n for n, pred in hnsw_de_chunks.items() if f"'{collection}'" in pred]
    assert propios, (
        f"{collection} no tiene un HNSW parcial: su búsqueda densa recorre un grafo "
        f"compartido y post-filtrado. HNSW presentes: {hnsw_de_chunks}"
    )


def test_no_hnsw_spans_every_collection(hnsw_de_chunks):
    globales = [n for n, pred in hnsw_de_chunks.items() if not pred]
    assert not globales, (
        f"queda un HNSW sin predicado ({globales}): el planner lo puede elegir y "
        "vuelve el post-filtrado entre colecciones"
    )
