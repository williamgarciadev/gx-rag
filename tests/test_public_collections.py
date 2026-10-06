"""El gate de COLECCION del transporte público.

POR QUE HACE FALTA UN GATE APARTE DEL DE TOOLS: PUBLIC_TOOL_NAMES cubre la dimensión
tool, y test_server_write_tools.py verifica que ninguna write tool llegue a internet.
Pero `search_docs` ES pública y recibe `collection` como PARAMETRO, validado solo
contra KNOWN_COLLECTIONS. Sin este gate, sumar un nombre a esa tupla alcanzaba para
publicar una colección entera en el endpoint de internet — sin tocar http_server.py y
sin que ningún test se pusiera rojo.

El caso concreto que lo motiva: indexar los objetos de una Knowledge Base GeneXus de
banca. Eso no es documentación pública, y la diferencia no puede depender de que
alguien se acuerde.
"""

from __future__ import annotations

import pytest

import src.server as srv

pytestmark = pytest.mark.unit


def test_the_kb_collection_is_not_public():
    """La colección de objetos de KB nunca se sirve en internet."""
    assert "gx_kb_objects" in srv.NON_PUBLIC_COLLECTIONS
    assert "gx_kb_objects" not in srv.PUBLIC_COLLECTIONS


def test_public_collections_is_declared_by_subtraction():
    """El default seguro es "no se publica", y eso exige restar, no enumerar.

    Con una lista blanca, agregar una colección y olvidarse de actualizarla la deja
    PUBLICA. Con sustracción, la deja privada. La diferencia importa el día que
    alguien agregue una colección apurado.
    """
    assert srv.PUBLIC_COLLECTIONS == frozenset(srv.KNOWN_COLLECTIONS) - srv.NON_PUBLIC_COLLECTIONS
    assert srv.PUBLIC_COLLECTIONS <= frozenset(srv.KNOWN_COLLECTIONS)


def test_a_non_public_collection_is_rejected():
    mensaje = srv.reject_non_public_collection("gx_kb_objects")

    assert mensaje is not None
    assert "gx_kb_objects" in mensaje


def test_a_public_collection_passes_through():
    for coleccion in srv.PUBLIC_COLLECTIONS:
        assert srv.reject_non_public_collection(coleccion) is None


def test_an_absent_collection_argument_is_not_rejected():
    """No pasar `collection` cae en el default del schema, que es documentación."""
    assert srv.reject_non_public_collection(None) is None


def test_a_policy_rejection_does_not_read_as_an_empty_corpus():
    """El modo de falla que importa: que el agente concluya sobre el CORPUS.

    Un rechazo de acceso que se lee como "no hay nada" hace que el agente propague
    aguas abajo una afirmación sobre el contenido, cuando el store nunca se consultó.
    Es la misma clase de mentira silenciosa que el resto de este repo previene.
    """
    mensaje = srv.reject_non_public_collection("gx_kb_objects")

    assert "Sin resultados" not in mensaje
    assert "vacía" not in mensaje.replace("está vacía", "")
    # Y tiene que decir explícitamente que no se leyó el store.
    assert "ANTES de consultar" in mensaje


def test_the_rejection_names_the_collections_that_are_available():
    """Como list_methods: informar lo que SI existe, para no invitar a otro intento
    que garantiza vacío."""
    mensaje = srv.reject_non_public_collection("gx_kb_objects")

    for coleccion in srv.PUBLIC_COLLECTIONS:
        assert coleccion in mensaje


def test_the_public_transport_wires_the_collection_gate():
    """Que la función exista no sirve si http_server no la llama."""
    import inspect

    import src.http_server as http_srv

    fuente = inspect.getsource(http_srv.build_public_server)
    assert "reject_non_public_collection" in fuente
