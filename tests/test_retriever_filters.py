"""Filtros por metadatos: se aplican DENTRO de cada mitad del RRF, no después.

POR QUE ESTO NO ES UN DETALLE DE ESTILO. Cada mitad del RRF trae solo `pool`
candidatos y recién ahí se fusiona. Si el filtro se aplicara después de la fusión,
esos candidatos se llenan de chunks que el filtro va a descartar, y una búsqueda
FILTRADA devuelve PEOR que la sin filtrar — a veces cero. El agente lee "casi no hay
transacciones sobre esto", que es una afirmación sobre el corpus derivada de un
artefacto del pipeline.

El escenario está armado para que el post-filtrado falle de forma inequívoca: los
chunks del tipo EQUIVOCADO son los más cercanos al vector de la query, así que copan
el pool denso. `test_..._not_after_the_fusion` mide las dos estrategias sobre los
mismos datos y muestra la diferencia.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration

KB = "gx_kb_objects"
KB_NAME = "MatikaErp_3003"

# Vector de la query: e_0. Los "Procedure" quedan casi encima; los "Transaction",
# claramente más lejos. Con <=> (coseno) lo que ordena es el ángulo, así que no hace
# falta normalizar.
_QVEC = [1.0] + [0.0] * 1023
_CERCA = [1.0, 0.05] + [0.0] * 1022   # cos ~0.9988
_LEJOS = [1.0, 0.90] + [0.0] * 1022   # cos ~0.743

RUIDO = 40   # copan el pool denso (que es max(30, top_k*3))
OBJETIVO = 3


def _lit(v: list[float]) -> str:
    return "[" + ",".join(str(x) for x in v) + "]"


@pytest.fixture
def kb_sembrada(pg_store, monkeypatch):
    """40 Procedures muy cerca del vector de query + 3 Transactions lejos.

    Todos comparten el mismo texto para que la mitad léxica también los encuentre a
    todos: así el test aísla el efecto del filtro y no el del ranking.
    """
    from src.vectorstore import pg_retriever

    texto = "mayorizar asiento contable"

    with pg_store.pool.connection() as conn:
        for i in range(RUIDO):
            conn.execute(
                "INSERT INTO chunks (chunk_id, collection, article_id, chunk_index,"
                " title, content, source, url, genre, content_hash, embedding, meta)"
                " VALUES (%s,%s,%s,0,'',%s,'','','','h',%s::vector,%s::jsonb)",
                (f"ruido{i}_c0", KB, f"kb:{KB_NAME}:Procedure:P{i}", texto,
                 _lit(_CERCA),
                 json.dumps({"obj_name": f"P{i}", "obj_type": "Procedure",
                             "chunk_kind": "logic", "kb_name": KB_NAME})),
            )
        for i in range(OBJETIVO):
            conn.execute(
                "INSERT INTO chunks (chunk_id, collection, article_id, chunk_index,"
                " title, content, source, url, genre, content_hash, embedding, meta)"
                " VALUES (%s,%s,%s,0,'',%s,'','','','h',%s::vector,%s::jsonb)",
                (f"trn{i}_c0", KB, f"kb:{KB_NAME}:Transaction:T{i}", texto,
                 _lit(_LEJOS),
                 json.dumps({"obj_name": f"T{i}", "obj_type": "Transaction",
                             "chunk_kind": "structure", "kb_name": KB_NAME})),
            )

    monkeypatch.setattr(pg_retriever.emb, "embed", lambda q: _QVEC)
    return pg_retriever


def test_a_selective_filter_still_returns_its_matches(kb_sembrada):
    """3 Transactions entre 43 chunks: el filtro tiene que encontrarlas."""
    hits = kb_sembrada.hybrid_search(
        "mayorizar asiento", collection=KB, top_k=5,
        filters={"obj_type": ["Transaction"]},
    )

    assert len(hits) == OBJETIVO
    assert {h["meta"]["obj_type"] for h in hits} == {"Transaction"}


def test_the_filter_runs_inside_each_cte_not_after_the_fusion(kb_sembrada):
    """La medición que justifica el diseño, sobre los MISMOS datos.

    Estrategia real (filtro dentro de cada CTE) vs. la que se descartó (filtrar el
    resultado de una búsqueda sin filtrar, que es lo que haría un post-filtro).
    """
    dentro = kb_sembrada.hybrid_search(
        "mayorizar asiento", collection=KB, top_k=5,
        filters={"obj_type": ["Transaction"]},
    )

    # Simulación del post-filtrado: se pide sin filtrar y se descarta después.
    sin_filtrar = kb_sembrada.hybrid_search("mayorizar asiento", collection=KB, top_k=5)
    despues = [h for h in sin_filtrar if h["meta"].get("obj_type") == "Transaction"]

    assert len(dentro) == OBJETIVO
    assert len(despues) == 0, (
        "el escenario no está reproduciendo el problema: el post-filtrado tendría que "
        "quedarse sin Transactions porque los Procedures copan el pool"
    )


def test_an_unfiltered_search_is_unaffected(kb_sembrada):
    """filters=None deja todos los predicados en NULL: no cambia nada.

    Es lo que protege a los 3 llamadores que ya existen (server.py, api.py,
    eval_bt_docs.py) de una regresión silenciosa.
    """
    hits = kb_sembrada.hybrid_search("mayorizar asiento", collection=KB, top_k=5)

    assert len(hits) == 5
    assert {h["meta"]["obj_type"] for h in hits} == {"Procedure"}


def test_filters_compose(kb_sembrada):
    hits = kb_sembrada.hybrid_search(
        "mayorizar asiento", collection=KB, top_k=10,
        filters={"obj_type": ["Transaction"], "chunk_kind": ["structure"]},
    )

    assert len(hits) == OBJETIVO

    ninguno = kb_sembrada.hybrid_search(
        "mayorizar asiento", collection=KB, top_k=10,
        filters={"obj_type": ["Transaction"], "chunk_kind": ["logic"]},
    )

    assert ninguno == []


def test_a_string_filter_is_accepted_like_a_single_item_list(kb_sembrada):
    """Comodidad para el llamador: obj_type="Transaction" == ["Transaction"]."""
    uno = kb_sembrada.hybrid_search(
        "mayorizar asiento", collection=KB, top_k=5, filters={"obj_type": "Transaction"}
    )

    assert len(uno) == OBJETIVO


def test_kb_name_scopes_two_kbs_in_the_same_collection(pg_store, monkeypatch):
    """El aislamiento entre KBs, que es la razón de que kb_name sea obligatorio.

    Sin él, un hit de otra KB haría que el agente escriba código con nombres de
    objetos que no existen en la KB activa — peor que no recuperar nada.
    """
    from src.vectorstore import pg_retriever

    with pg_store.pool.connection() as conn:
        for kb in ("MatikaErp_3003", "KBActivas"):
            conn.execute(
                "INSERT INTO chunks (chunk_id, collection, article_id, chunk_index,"
                " title, content, source, url, genre, content_hash, embedding, meta)"
                " VALUES (%s,%s,%s,0,'','factura cliente','','','','h',%s::vector,%s::jsonb)",
                (f"{kb}_c0", KB, f"kb:{kb}:Transaction:Factura", _lit(_CERCA),
                 json.dumps({"obj_name": "Factura", "obj_type": "Transaction",
                             "chunk_kind": "structure", "kb_name": kb})),
            )

    monkeypatch.setattr(pg_retriever.emb, "embed", lambda q: _QVEC)

    hits = pg_retriever.hybrid_search(
        "factura", collection=KB, top_k=5, filters={"kb_name": ["KBActivas"]}
    )

    assert len(hits) == 1
    assert hits[0]["meta"]["kb_name"] == "KBActivas"


def test_an_unknown_filter_key_is_rejected_not_ignored():
    """Ignorarla devolvería resultados SIN filtrar que el llamador cree filtrados."""
    from src.vectorstore.pg_retriever import filter_params

    with pytest.raises(ValueError) as exc:
        filter_params({"obj_typo": ["Transaction"]})

    assert "obj_typo" in str(exc.value)
    assert "obj_type" in str(exc.value)  # nombra las válidas
    assert "No se consulto el store" in str(exc.value)


def test_an_empty_filter_list_is_rejected():
    """`= ANY('{}')` es FALSE para toda fila: "Sin resultados" por un argumento vacío,
    que el agente leería como evidencia sobre el corpus."""
    from src.vectorstore.pg_retriever import filter_params

    with pytest.raises(ValueError) as exc:
        filter_params({"obj_type": []})

    assert "vacio" in str(exc.value).lower()


def test_the_filter_block_appears_in_both_halves():
    """Una sola constante interpolada dos veces.

    Escribirlo dos veces a mano sería la forma de que las mitades filtren distinto, y
    ese bug no se ve: la búsqueda devuelve resultados, solo que la léxica trae chunks
    que la densa ya descartó.
    """
    from src.vectorstore import pg_retriever

    assert pg_retriever._SEARCH_SQL.count(pg_retriever._META_FILTER) == 2


def test_every_declared_filter_key_is_wired_into_the_sql():
    """Una clave en FILTER_KEYS sin predicado en el SQL se aceptaría y no filtraría."""
    from src.vectorstore import pg_retriever

    for clave in pg_retriever.FILTER_KEYS:
        assert f"%(f_{clave})s" in pg_retriever._META_FILTER, f"{clave} sin predicado"
