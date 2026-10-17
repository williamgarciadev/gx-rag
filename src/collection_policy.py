"""
Política de colecciones: qué colecciones existen y qué se puede hacer con cada una.

Un solo módulo, sin base de datos, que consultan los transportes (server.py,
http_server.py, api.py vía tool_requests), la ingesta (kb_loader) y los CLIs.
Antes estaba repartida en cinco lugares y uno mentía: config.CollectionsConfig
listaba `cross_ref`, no tenía `gx_kb_objects` y no lo leía nadie.
"""

from __future__ import annotations

import argparse

# Colecciones que el store puede contener. Fuente ÚNICA: alimenta el enum de las
# read tools Y el chequeo de runtime, porque el SDK no valida el inputSchema — un
# `gx_doc` mal escrito llegaba al SQL, no matcheaba nada y volvía como "Sin
# resultados", que el agente lee como evidencia sobre el CORPUS y no sobre su
# propio argumento.
KNOWN_COLLECTIONS = ("gx_docs", "bt_docs", "gx_syntax", "bt_apis", "gx_kb_objects")
DEFAULT_COLLECTION = "gx_docs"

# La coleccion de objetos de Knowledge Base exige `kb_name` en los filtros. Es lo que
# hace imposible mezclar dos KBs sin darse cuenta: un hit de la KB equivocada haria
# que el agente escriba codigo con nombres de objetos que no existen en la KB activa,
# y eso es PEOR que no recuperar nada. El CHECK de sql/003 garantiza que la clave
# exista en cada fila; esto garantiza que la consulta la use.
KB_COLLECTION = "gx_kb_objects"
COLLECTIONS_REQUIRING_KB_NAME = frozenset({KB_COLLECTION})

# Colecciones a las que puede apuntar una ingesta por MCP (add_from_url). Alimenta
# el enum de la tool Y el chequeo de runtime, así schema y validación no divergen.
# Los CLIs de ingesta local no pasan por acá: cargan bt_docs a propósito.
WRITABLE_COLLECTIONS = frozenset({"gx_docs", "gx_syntax"})

# Colecciones que NO se sirven en el transporte público, y su complemento.
#
# POR QUE HACE FALTA, y por qué no alcanza PUBLIC_TOOL_NAMES: ese frozenset cubre
# la dimensión TOOL, y del lado write está bien cubierto. Pero `search_docs` YA es
# público y recibe `collection` como PARAMETRO, validado solo contra
# KNOWN_COLLECTIONS. O sea: sumar un nombre a esa tupla alcanzaba para publicar
# una colección en internet, sin tocar http_server.py y sin que ningún test se
# ponga rojo. El contenido de una KB de banca no puede depender de eso.
#
# SE DECLARA POR SUSTRACCION, no por enumeración de lo público: el default seguro
# tiene que ser "no se publica". Con una lista blanca, agregar una colección y
# olvidarse de la lista la deja pública; con esta forma, la deja privada.
NON_PUBLIC_COLLECTIONS = frozenset({KB_COLLECTION})
PUBLIC_COLLECTIONS = frozenset(KNOWN_COLLECTIONS) - NON_PUBLIC_COLLECTIONS


def reject_unknown_collection(collection: str) -> str | None:
    """Mensaje de rechazo si la colección no existe; None si es válida."""
    if collection in KNOWN_COLLECTIONS:
        return None
    return (
        f"Colección desconocida '{collection}'. "
        f"Válidas: {', '.join(KNOWN_COLLECTIONS)}.\n"
        "Esto NO dice nada sobre el corpus: se rechazó el argumento ANTES de "
        "consultar el store. Corrige el nombre y repite la llamada."
    )


def reject_non_public_collection(collection: str | None) -> str | None:
    """Mensaje de rechazo si la colección no se sirve en público; None si sí.

    Es el gate de la dimensión COLECCION del transporte público, complementario
    al de la dimensión TOOL que hace PUBLIC_TOOL_NAMES.

    `None` (colección no pasada) NO se rechaza: cae en el default del schema, que
    siempre es una colección de documentación.
    """
    if collection is None or collection in PUBLIC_COLLECTIONS:
        return None
    return (
        f"Colección '{collection}' no disponible en el endpoint público. "
        f"Disponibles: {', '.join(sorted(PUBLIC_COLLECTIONS))}.\n"
        # Sin esta línea un rechazo de POLITICA se lee como una colección vacía, y
        # el agente concluye sobre el corpus a partir de una decisión de acceso.
        "Esto NO dice nada sobre su contenido: se rechazó ANTES de consultar el "
        "store. No concluyas que está vacía ni que el tema no existe."
    )


def add_collection_argument(
    parser: argparse.ArgumentParser,
    *,
    default: str | None = DEFAULT_COLLECTION,
    help: str = "colección destino",
) -> None:
    """`--collection` para un CLI, validado contra KNOWN_COLLECTIONS.

    Sin `choices`, un nombre mal escrito llegaba al SQL y el CLI reportaba
    "0 chunks" o "no encontrado", indistinguible de una ausencia real.
    """
    parser.add_argument("--collection", default=default, choices=KNOWN_COLLECTIONS, help=help)
