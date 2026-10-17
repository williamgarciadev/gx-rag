"""Pedidos de las tools de lectura: validar, acotar y rechazar UNA vez.

Los tres transportes (MCP stdio, MCP público, API admin) pasan por acá. Antes
cada uno validaba a su manera y api.py dejaba buscar en gx_kb_objects sin
kb_name, el aislamiento multi-KB que el MCP sí impone.
"""

import pytest

from src.tool_requests import (
    RequestRejected,
    parse_article,
    parse_document,
    parse_search,
)

pytestmark = pytest.mark.unit


def test_search_con_defaults():
    r = parse_search({"query": "for each"})
    assert (r.query, r.collection, r.top_k, r.snippet_chars) == ("for each", "gx_docs", 5, 600)
    assert r.filters is None and r.max_per_article is None and r.rerank is False


@pytest.mark.parametrize("crudo, esperado", [(0, 1), (-3, 1), (999, 50), ("x", 5), (None, 5), (7, 7)])
def test_top_k_se_acota(crudo, esperado):
    args = {"query": "q"} if crudo is None else {"query": "q", "top_k": crudo}
    assert parse_search(args).top_k == esperado


@pytest.mark.parametrize("crudo, esperado", [(10, 100), (99999, 4000), ("x", 600), (800, 800)])
def test_snippet_chars_se_acota(crudo, esperado):
    assert parse_search({"query": "q", "snippet_chars": crudo}).snippet_chars == esperado


@pytest.mark.parametrize("crudo, esperado", [(0, None), (-1, None), ("x", None), (2, 2)])
def test_max_per_article_invalido_es_sin_limite(crudo, esperado):
    assert parse_search({"query": "q", "max_per_article": crudo}).max_per_article == esperado


def test_coleccion_desconocida_se_rechaza_en_las_tres_tools():
    for parse, args in (
        (parse_search, {"query": "q", "collection": "gx_doc"}),
        (parse_document, {"doc_id": "x_c0", "collection": "gx_doc"}),
        (parse_article, {"article_id": "x", "collection": "gx_doc"}),
    ):
        with pytest.raises(RequestRejected, match="Colección desconocida 'gx_doc'"):
            parse(args)


def test_kb_objects_sin_kb_name_se_rechaza_antes_del_store():
    with pytest.raises(RequestRejected, match="kb_name"):
        parse_search({"query": "q", "collection": "gx_kb_objects"})


def test_kb_objects_con_kb_name_pasa():
    r = parse_search({"query": "q", "collection": "gx_kb_objects", "filters": {"kb_name": "KBMicro"}})
    assert r.filters == {"kb_name": "KBMicro"}


def test_filtro_desconocido_se_rechaza_al_parsear():
    """Antes el ValueError salía de dentro de hybrid_search, después de cargar el
    modelo. Rechazar al parsear no paga e5-large por un argumento inválido."""
    with pytest.raises(RequestRejected, match="Filtro"):
        parse_search({"query": "q", "filters": {"kbname": "x"}})


def test_document_y_article_con_defaults():
    assert parse_document({"doc_id": "a_c1"}) == parse_document({"doc_id": "a_c1", "collection": "gx_docs"})
    assert parse_article({"article_id": "a"}).collection == "gx_docs"
