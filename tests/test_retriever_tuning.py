"""El tuning de búsqueda vectorial filtrada está cableado en hybrid_search.

QUÉ PROTEGE ESTO, concretamente: que un refactor de hybrid_search no se lleve el
`SET LOCAL hnsw.iterative_scan`. Sin esa línea la mitad densa del RRF devuelve CERO
filas cuando los vecinos más cercanos del vector de query pertenecen a OTRA
colección — medido sobre el corpus real (51.211 chunks) con la query 'saldo contable
de la cuenta' buscando en gx_docs: 0 filas densas sin el tuning, 30 con él, y de
35 ms a 10 ms.

QUÉ **NO** PRUEBA: el recall en sí. Reproducir el under-return exige suficientes
filas para que el planner elija el HNSW por sobre un seq scan (miles), y el
resultado quedaría colgado de una decisión del planner en vez de del código. Eso lo
cubre la medición documentada arriba, no este test. Acá se verifica la propiedad
estructural que un refactor puede romper en silencio, que es la que se rompe de
verdad.

Sin Postgres a propósito: un fake del pool alcanza para afirmar QUÉ se ejecuta y EN
QUÉ ORDEN, y así el test corre siempre, no solo cuando hay motor levantado.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from src.vectorstore import pg_retriever


class _FakeCursor:
    def __init__(self, ejecutadas: list[str]):
        self._ejecutadas = ejecutadas

    def execute(self, sql, params=None, **kwargs):
        self._ejecutadas.append(sql)

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeTransaction:
    def __init__(self, ejecutadas: list[str]):
        self._ejecutadas = ejecutadas

    def __enter__(self):
        self._ejecutadas.append("<BEGIN>")
        return self

    def __exit__(self, *exc):
        self._ejecutadas.append("<COMMIT>")
        return False


class _FakeConnection:
    def __init__(self, ejecutadas: list[str]):
        self._ejecutadas = ejecutadas

    def transaction(self):
        return _FakeTransaction(self._ejecutadas)

    def cursor(self):
        return _FakeCursor(self._ejecutadas)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakePool:
    def __init__(self):
        self.ejecutadas: list[str] = []

    def connection(self):
        return _FakeConnection(self.ejecutadas)


@pytest.fixture
def pool_espia(monkeypatch):
    """Pool falso que registra el SQL ejecutado, sin tocar Postgres."""
    pool = _FakePool()
    monkeypatch.setattr(pg_retriever, "get_pool", lambda: pool)
    monkeypatch.setattr(pg_retriever.emb, "embed_query", lambda q: [0.0] * 1024)
    return pool


def test_the_tuning_statement_targets_iterative_scan():
    """La constante existe y apunta al mecanismo correcto, no a ef_search.

    Subir ef_search también funcionaba en la medición (30 filas con ef_search=1000),
    pero paga el costo en TODA consulta, filtrada o no. iterative_scan solo reintenta
    cuando el grafo se agotó antes de llenar el LIMIT.
    """
    assert "hnsw.iterative_scan" in pg_retriever._TUNING_SQL
    assert "relaxed_order" in pg_retriever._TUNING_SQL
    # SET LOCAL y no SET: el alcance tiene que morir con la transacción. Una conexión
    # devuelta al pool con el GUC pegado se lo lleva al siguiente que la tome.
    assert pg_retriever._TUNING_SQL.strip().upper().startswith("SET LOCAL")


def test_hybrid_search_applies_the_tuning_before_the_search(pool_espia):
    """El orden importa: un SET LOCAL después de la query no afecta a esa query."""
    pg_retriever.hybrid_search("cualquier cosa")

    sql_ejecutado = [s for s in pool_espia.ejecutadas if not s.startswith("<")]
    assert len(sql_ejecutado) == 2, f"esperaba tuning + búsqueda, hubo: {sql_ejecutado}"
    assert "hnsw.iterative_scan" in sql_ejecutado[0]
    assert "WITH dense AS" in sql_ejecutado[1]


def test_the_tuning_runs_inside_an_explicit_transaction(pool_espia):
    """SET LOCAL exige un bloque de transacción; sin él es un no-op silencioso.

    Y el bloque tiene que ABRIR antes del tuning: si el SET LOCAL cayera fuera de la
    transacción, PostgreSQL lo aceptaría con un warning y el GUC no aplicaría a nada.
    """
    pg_retriever.hybrid_search("cualquier cosa")

    traza = pool_espia.ejecutadas
    assert traza[0] == "<BEGIN>", f"la transacción no abre primero: {traza[:2]}"
    assert traza[-1] == "<COMMIT>", f"la transacción no cierra: {traza[-2:]}"
    idx_tuning = next(i for i, s in enumerate(traza) if "iterative_scan" in s)
    assert 0 < idx_tuning < len(traza) - 1
