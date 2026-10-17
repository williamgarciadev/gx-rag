"""
Pedidos de las tools de lectura: validar, acotar y rechazar UNA vez.

Los tres transportes —MCP stdio (server.py), MCP público (http_server.py) y la
API admin (api.py)— reciben argumentos crudos y los pasan por acá. Lo que sale
es un pedido válido o un RequestRejected con un mensaje accionable para el
agente. Antes cada transporte validaba a su manera, y api.py dejaba buscar en
gx_kb_objects sin kb_name: el aislamiento multi-KB que el MCP sí impone.

Todo rechazo ocurre ANTES de consultar el store, y el mensaje lo dice: un
argumento mal escrito no puede volver como "Sin resultados", que el agente lee
como evidencia sobre el corpus y no sobre su propio argumento.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.collection_policy import (
    COLLECTIONS_REQUIRING_KB_NAME,
    DEFAULT_COLLECTION,
    reject_unknown_collection,
)

# Límites de search_docs. Viven acá y no en el handler de cada transporte: api.py
# topaba top_k en 20 y el MCP en 50.
TOP_K_DEFAULT, TOP_K_MIN, TOP_K_MAX = 5, 1, 50
SNIPPET_DEFAULT, SNIPPET_MIN, SNIPPET_MAX = 600, 100, 4000


class RequestRejected(ValueError):
    """Argumento inválido, rechazado antes del store. str() es el mensaje para el agente."""


@dataclass(frozen=True)
class SearchRequest:
    query: str
    collection: str = DEFAULT_COLLECTION
    top_k: int = TOP_K_DEFAULT
    snippet_chars: int = SNIPPET_DEFAULT
    min_score: float | None = None
    filters: dict | None = None
    rerank: bool = False
    max_per_article: int | None = None


@dataclass(frozen=True)
class DocumentRequest:
    doc_id: str
    collection: str = DEFAULT_COLLECTION


@dataclass(frozen=True)
class ArticleRequest:
    article_id: str
    collection: str = DEFAULT_COLLECTION


def parse_search(args: dict) -> SearchRequest:
    collection = _collection(args)
    filters = args.get("filters")
    if rechazo := reject_missing_kb_name(collection, filters):
        raise RequestRejected(rechazo)
    # Validar los filtros acá, y no dentro de hybrid_search, evita pagar la carga
    # de e5-large (~2 GB en frío) por un argumento que ya sabemos inválido.
    from src.vectorstore.pg_retriever import filter_params

    try:
        filter_params(filters)
    except ValueError as exc:
        raise RequestRejected(str(exc)) from exc

    max_per_article = _int_or(args.get("max_per_article"), None)
    return SearchRequest(
        query=args.get("query", ""),
        collection=collection,
        top_k=_clamp(_int_or(args.get("top_k"), TOP_K_DEFAULT), TOP_K_MIN, TOP_K_MAX),
        snippet_chars=_clamp(
            _int_or(args.get("snippet_chars"), SNIPPET_DEFAULT), SNIPPET_MIN, SNIPPET_MAX
        ),
        min_score=args.get("min_score"),
        filters=filters,
        rerank=bool(args.get("rerank", False)),
        max_per_article=max_per_article if max_per_article and max_per_article > 0 else None,
    )


def parse_document(args: dict) -> DocumentRequest:
    return DocumentRequest(doc_id=args.get("doc_id", ""), collection=_collection(args))


def parse_article(args: dict) -> ArticleRequest:
    return ArticleRequest(article_id=args.get("article_id", ""), collection=_collection(args))




def reject_missing_kb_name(collection: str, filters: dict | None) -> str | None:
    """Mensaje de rechazo si falta `kb_name` en una colección que lo exige.

    Es la barrera contra el peor modo de falla de una colección multi-KB: recuperar la
    ficha de un objeto de OTRA KB y escribir código con nombres que no existen en la
    KB activa. Un aislamiento que depende de que el llamador se acuerde de filtrar no
    es un aislamiento; esto lo vuelve un error ruidoso.
    """
    if collection not in COLLECTIONS_REQUIRING_KB_NAME:
        return None
    if filters and filters.get("kb_name"):
        return None

    disponibles = ""
    try:
        from src.vectorstore.pg_collections import meta_facets

        facetas = meta_facets(collection, ("kb_name",))
        if facetas.get("kb_name"):
            nombres = " · ".join(f"{v} ({n} chunks)" for v, n in facetas["kb_name"])
            disponibles = f"\nKBs indexadas en esta colección: {nombres}"
    except Exception:  # pragma: no cover - el mensaje no puede depender de la DB
        pass

    return (
        f"Falta el filtro obligatorio 'kb_name' para la colección '{collection}'.\n"
        "Esta colección puede contener objetos de MAS DE UNA Knowledge Base, y un "
        "resultado de la KB equivocada te haría escribir código con nombres de objetos "
        "que no existen en la KB activa — peor que no recuperar nada.\n"
        'Pasa filters={"kb_name": "<nombre>"}. Si no sabes cuál es la KB activa, '
        "consulta genexus_whoami en el MCP de GeneXus (campo kb.active)."
        f"{disponibles}\n"
        "NOTA: esto se rechazó ANTES de consultar el store. No dice nada sobre su "
        "contenido."
    )


def _collection(args: dict) -> str:
    collection = args.get("collection") or DEFAULT_COLLECTION
    if rechazo := reject_unknown_collection(collection):
        raise RequestRejected(rechazo)
    return collection


def _int_or(raw, default):
    try:
        return int(raw) if raw is not None else default
    except (ValueError, TypeError):
        return default


def _clamp(value: int, low: int, high: int) -> int:
    return min(max(low, value), high)
