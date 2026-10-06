"""La superficie MCP del filtrado: schema, rechazos y el texto de "sin resultados".

La estrategia anti-alucinación de este repo vive en las descripciones de las tools y
en los TEXTOS DE RESPUESTA (`src/server.py:7-13`). Estos tests tratan esos textos como
contrato, igual que test_interface_contract.py: lo que importa no es solo que la
búsqueda filtre bien (eso lo cubre test_retriever_filters.py), sino que cuando NO
devuelve nada, el llamador no concluya algo falso sobre el corpus.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.unit

import src.server as srv


def _call(name: str, **arguments) -> str:
    """asyncio.run y no pytest-asyncio: la suite no tiene otras pruebas async."""
    content = asyncio.run(srv.dispatch_tool(name, arguments))
    return content[0].text


@pytest.fixture
def store_intocable(monkeypatch):
    """Cualquier llegada al store es un fallo: estos rechazos son previos."""
    from src.vectorstore import pg_collections, pg_retriever

    def _explota(*a, **k):  # pragma: no cover - no debe ejecutarse
        raise AssertionError("el rechazo tenía que ocurrir antes de tocar el store")

    monkeypatch.setattr(pg_retriever, "hybrid_search", _explota)
    monkeypatch.setattr(pg_collections, "meta_facets", lambda *a, **k: {})
    monkeypatch.setattr(pg_collections, "collection_counts", lambda: [])


# ─── schema ───────────────────────────────────────────────────────────────────


def test_the_kb_collection_is_in_the_declared_enum():
    """El enum de la tool y KNOWN_COLLECTIONS no pueden divergir."""
    schema = next(t for t in srv.READ_TOOLS if t.name == "search_docs").input_schema

    assert srv.KB_COLLECTION in schema["properties"]["collection"]["enum"]
    assert schema["properties"]["collection"]["enum"] == list(srv.KNOWN_COLLECTIONS)


def test_search_docs_declares_the_filters_parameter():
    schema = next(t for t in srv.READ_TOOLS if t.name == "search_docs").input_schema

    assert "filters" in schema["properties"]
    # No es required: la búsqueda sin filtros sigue siendo el caso normal.
    assert "filters" not in schema.get("required", [])


def test_the_filters_description_names_every_declared_key():
    """Una clave soportada que la descripción no nombra es una clave que nadie usa."""
    from src.vectorstore.pg_retriever import FILTER_KEYS

    schema = next(t for t in srv.READ_TOOLS if t.name == "search_docs").input_schema
    texto = schema["properties"]["filters"]["description"]

    for clave in FILTER_KEYS:
        assert clave in texto, f"la descripción no nombra '{clave}'"


# ─── kb_name obligatorio ──────────────────────────────────────────────────────


def test_the_kb_collection_requires_kb_name(store_intocable):
    salida = _call("search_docs", query="factura", collection=srv.KB_COLLECTION)

    assert "kb_name" in salida
    assert "obligatorio" in salida.lower()


def test_the_kb_name_rejection_explains_the_real_risk(store_intocable):
    """No alcanza con decir "falta un parámetro": hay que decir por qué importa."""
    salida = _call("search_docs", query="factura", collection=srv.KB_COLLECTION)

    assert "no existen en la KB activa" in salida
    # Y tiene que aclarar que no se leyó el store, o se lee como corpus vacío.
    assert "ANTES de consultar" in salida


def test_the_kb_name_rejection_points_at_how_to_find_the_active_kb(store_intocable):
    salida = _call("search_docs", query="factura", collection=srv.KB_COLLECTION)

    assert "genexus_whoami" in salida


def test_other_collections_do_not_require_kb_name():
    """La exigencia es de la colección de KB, no global."""
    assert srv._reject_missing_kb_name("gx_docs", None) is None
    assert srv._reject_missing_kb_name("bt_docs", {"origin": ["sac"]}) is None


def test_kb_name_present_passes_the_gate():
    assert (
        srv._reject_missing_kb_name(srv.KB_COLLECTION, {"kb_name": "MatikaErp_3003"})
        is None
    )


# ─── filtro inválido: rechazo accionable, no GENERIC_ERROR ────────────────────


def test_an_unknown_filter_key_reaches_the_caller_as_an_actionable_message(monkeypatch):
    """El ValueError de filter_params NO puede caer en el except genérico.

    Si se convirtiera en GENERIC_ERROR, el llamador buscaría la causa en el corpus o
    en el servidor, cuando el problema es su propio argumento.
    """
    salida = _call(
        "search_docs", query="factura", collection="gx_docs",
        filters={"obj_typo": ["Transaction"]},
    )

    assert srv.GENERIC_ERROR not in salida
    assert "obj_typo" in salida
    assert "obj_type" in salida  # nombra las válidas


def test_an_empty_filter_list_reaches_the_caller_too():
    salida = _call(
        "search_docs", query="factura", collection="gx_docs", filters={"obj_type": []}
    )

    assert srv.GENERIC_ERROR not in salida
    assert "vacio" in salida.lower()


# ─── el texto de "sin resultados" ─────────────────────────────────────────────


def test_an_empty_filtered_search_says_the_collection_is_not_empty(monkeypatch):
    """El bug de comunicación que este test previene.

    Sin esta rama, un filtro que no matchea devuelve "Sin resultados en 'X'.
    Colecciones con contenido: …", que le dice al agente que mire OTRA colección —
    cuando la colección era la correcta y el filtro tenía un typo.
    """
    from src.vectorstore import pg_collections, pg_retriever

    monkeypatch.setattr(pg_retriever, "hybrid_search", lambda *a, **k: [])
    monkeypatch.setattr(
        pg_collections, "collection_counts", lambda: [("gx_kb_objects", 10412)]
    )
    monkeypatch.setattr(
        pg_collections,
        "meta_facets",
        lambda col, claves, **k: {
            "obj_type": [("Procedure", 5104), ("Transaction", 612)],
        },
    )

    salida = _call(
        "search_docs", query="mayorizar", collection=srv.KB_COLLECTION,
        filters={"kb_name": "MatikaErp_3003", "obj_type": ["Transacion"]},
    )

    assert "NO está vacía" in salida
    assert "el filtro es lo que no matcheó" in salida
    # Informa los valores que SI existen, para no invitar a otro intento vacío.
    assert "Transaction" in salida
    # Y NO debe sugerir cambiar de colección, que es el consejo equivocado acá.
    assert "Colecciones con contenido" not in salida


def test_an_empty_unfiltered_search_still_hints_other_collections(monkeypatch):
    """La rama vieja sigue intacta: sin filtros, el hint correcto es otro."""
    from src.vectorstore import pg_collections, pg_retriever

    monkeypatch.setattr(pg_retriever, "hybrid_search", lambda *a, **k: [])
    monkeypatch.setattr(
        pg_collections, "collection_counts", lambda: [("gx_docs", 50063)]
    )

    salida = _call("search_docs", query="zzqx", collection="bt_apis")

    assert "Colecciones con contenido" in salida


def test_the_filtered_empty_text_does_not_claim_the_topic_is_absent(monkeypatch):
    """Un filtro que no matchea no prueba que el tema falte."""
    from src.vectorstore import pg_collections, pg_retriever

    monkeypatch.setattr(pg_retriever, "hybrid_search", lambda *a, **k: [])
    monkeypatch.setattr(pg_collections, "collection_counts", lambda: [("gx_docs", 10)])
    monkeypatch.setattr(pg_collections, "meta_facets", lambda *a, **k: {})

    salida = _call(
        "search_docs", query="x", collection="gx_docs", filters={"origin": ["wwp"]}
    )

    assert "verificalo sin" in salida


# ─── render de metadatos ──────────────────────────────────────────────────────


def test_a_kb_hit_renders_its_object_identity():
    linea = srv._meta_line(
        {"obj_name": "PrcMayorizar", "obj_type": "Procedure", "kb_name": "MatikaErp_3003"}
    )

    assert "obj_name=PrcMayorizar" in linea
    assert "obj_type=Procedure" in linea


def test_a_doc_hit_renders_nothing():
    """meta {} en las colecciones de documentación: sin ramificar por colección."""
    assert srv._meta_line({}) == ""
    assert srv._meta_line(None) == ""


def test_the_related_array_is_not_rendered_inline():
    linea = srv._meta_line({"obj_name": "X", "related": ["A", "B"]})

    assert "obj_name=X" in linea
    assert "related" not in linea
