"""El benchmark de latencia tiene que medir la búsqueda que corre en producción.

Medido 2026-10-06: `scripts/bench_retrieval.py` ejecutaba `_SEARCH_SQL` por su
cuenta, y se apartaba de `hybrid_search` en tres puntos: sin `SET LOCAL
hnsw.iterative_scan`, sin `prepare=False` (psycopg auto-prepara en la 5.ª
ejecución y el plan genérico pierde los índices de meta: el bench repite cada
consulta, así que medía justo ese acantilado) y con `emb.embed` en vez de
`embed_query` (sin el perfil de embedding). Ahora los dos pasan por las mismas
funciones de `pg_retriever`, y estos tests fallan si alguno vuelve a ejecutar el
SQL por su lado.
"""

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def production_path(monkeypatch):
    """Registra lo que llega a las funciones de producción, sin modelo ni base."""
    from src.vectorstore import pg_retriever

    calls = {"vector": [], "execute": []}

    def _vector(query):
        calls["vector"].append(query)
        return "[0]"

    def _execute(params):
        calls["execute"].append(params)
        return []

    monkeypatch.setattr(pg_retriever, "query_vector", _vector)
    monkeypatch.setattr(pg_retriever, "execute_search", _execute)
    return calls


def test_bench_measures_through_the_production_search_path(production_path, monkeypatch):
    from scripts import bench_retrieval

    monkeypatch.setattr(bench_retrieval.emb.embed_texts, "cache_clear", lambda: None)

    bench_retrieval.medir("gx_docs", ["data provider"], repeat=1, top_k=5)

    assert production_path["vector"] == ["data provider"]
    (params,) = production_path["execute"]
    assert params["qvec"] == "[0]"
    assert params["collection"] == "gx_docs"


def test_hybrid_search_goes_through_the_same_functions(production_path):
    from src.vectorstore.pg_retriever import hybrid_search

    assert hybrid_search("data provider", collection="bt_docs") == []
    (params,) = production_path["execute"]
    assert params["collection"] == "bt_docs"
    assert params["qvec"] == "[0]"
