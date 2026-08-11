"""
gx_bt_rag.server — MCP server (stdio transport).

Retrieval semántico (ChromaDB + hybrid search) sobre documentación
GeneXus y Bantotal, con capa de validación contra SQLite.
"""

import logging

from collections.abc import Awaitable, Callable, Sequence

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

# ─── Server instance ────────────────────────────────────────────────────────────

APP_NAME = "gx-bt-rag-mcp"
VERSION = "0.1.0"

server = Server(APP_NAME)


# ─── Tools ────────────────────────────────────────────────────────────────────


# Read-only tools — safe for the public internet-facing transport.
READ_TOOLS: list[Tool] = [
    Tool(
        name="search_docs",
        description=(
            "Búsqueda híbrida (dense + BM25 + RRF) en la colección indicada "
            "de documentación GeneXus/Bantotal. Devuelve los fragmentos más "
            "relevantes con score. Si la colección está vacía, informa cuáles "
            "tienen contenido para reintentar."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Consulta en lenguaje natural o palabras clave.",
                },
                "collection": {
                    "type": "string",
                    "enum": ["gx_docs", "bt_docs", "gx_syntax", "bt_apis"],
                    "description": "Colección a buscar.",
                    "default": "gx_docs",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Cantidad de resultados a devolver.",
                    "default": 5,
                },
                "snippet_chars": {
                    "type": "integer",
                    "description": (
                        "Largo del fragmento por resultado. Subilo para ver la "
                        "sintaxis completa; usá get_article para el doc entero."
                    ),
                    "default": 600,
                },
                "min_score": {
                    "type": "number",
                    "description": (
                        "Piso de score RRF (rank-based, NO calibrado: no es "
                        "probabilidad). Filtro grueso, no umbral de confianza."
                    ),
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="get_article",
        description=(
            "Reensambla el documento COMPLETO a partir de sus chunks (orden "
            "chunk_index). Usalo tras search_docs, con el article_id, cuando "
            "el fragmento no alcanza para fundamentar sin alucinar."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "article_id": {
                    "type": "string",
                    "description": "ID de artículo (campo article_id/id de search_docs).",
                },
                "collection": {
                    "type": "string",
                    "description": "Colección donde está el artículo.",
                    "default": "gx_docs",
                },
            },
            "required": ["article_id"],
        },
    ),
    Tool(
        name="get_document",
        description=(
            "Obtiene el contenido completo de un documento previamente "
            "indexado, incluyendo su metadata y provenance."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "doc_id": {
                    "type": "string",
                    "description": "ID interno del documento en ChromaDB.",
                },
                "collection": {
                    "type": "string",
                    "description": "Colección donde está el documento.",
                    "default": "gx_docs",
                },
            },
            "required": ["doc_id"],
        },
    ),
    Tool(
        name="verify_syntax",
        description=(
            "Verifica si una firma, método o comando de GeneXus/Bantotal "
            "es válido contra el catálogo estructurado. "
            "Devuelve VÁLIDO / INVÁLIDO / NO_VERIFICABLE con procedencia."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "snippet": {
                    "type": "string",
                    "description": "Código o snippet a verificar.",
                },
                "language": {
                    "type": "string",
                    "enum": ["gx", "bt_api"],
                    "description": "Lenguaje/dialecto del snippet.",
                    "default": "gx",
                },
                "version": {
                    "type": "string",
                    "description": "Versión de GeneXus (gx16, gx18).",
                },
            },
            "required": ["snippet"],
        },
    ),
    Tool(
        name="list_methods",
        description=(
            "Lista todos los métodos/propiedades conocidos de un objeto "
            "GeneXus específico en el catálogo."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "object_type": {
                    "type": "string",
                    "description": "Tipo de objeto (Transaction, Procedure, etc.).",
                },
                "version": {
                    "type": "string",
                    "description": "Versión de GeneXus (gx16, gx18).",
                },
            },
            "required": ["object_type"],
        },
    ),
]


# Write tools — mutate the corpus. Kept OFF the public transport (add_from_url is
# an SSRF vector; ingestion requires a human gate). stdio/admin surface only.
WRITE_TOOLS: list[Tool] = [
    Tool(
        name="add_from_url",
        description=(
            "Ingiere un artículo de la wiki GeneXus desde su URL. "
            "Lo scrapea, clasifica, guarda en filesystem e indexa en ChromaDB."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL completa del artículo wiki.",
                },
                "collection": {
                    "type": "string",
                    "enum": ["gx_docs", "gx_syntax"],
                    "description": "Colección destino (sobreescribe clasificación automática si se indica).",
                },
            },
            "required": ["url"],
        },
    ),
    Tool(
        name="ingest_batch",
        description=(
            "Ingiere todos los archivos Markdown de un directorio local. "
            "Útil para procesar el corpus Bantotal ya curado."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Ruta al directorio con archivos .md.",
                },
                "collection": {
                    "type": "string",
                    "description": "Colección destino.",
                    "default": "gx_docs",
                },
            },
            "required": ["directory"],
        },
    ),
]


# Tool names allowed on the public internet-facing transport (read-only).
PUBLIC_TOOL_NAMES = frozenset(t.name for t in READ_TOOLS)


ToolDispatcher = Callable[[str, dict], Awaitable[list[TextContent]]]


def register_tools(
    srv: Server,
    tools: Sequence[Tool],
    dispatch: ToolDispatcher,
) -> None:
    """Cablea tools/list y tools/call en `srv`.

    mcp 2.0 elimino los decoradores @server.list_tools()/@server.call_tool() en
    favor de add_request_handler(method, params_type, handler), donde el handler
    recibe (contexto, params ya validados) y devuelve el Result completo en vez
    de la lista pelada.

    Se centraliza aca porque hay dos servidores que exponen el mismo par de
    handlers con distinta politica: el de stdio (read + write) y el publico
    (read-only, en http_server.build_public_server). Duplicar el cableado en
    ambos era el camino a que uno quede desincronizado.
    """

    async def _list(_ctx, _params: PaginatedRequestParams) -> ListToolsResult:
        return ListToolsResult(tools=list(tools))

    async def _call(_ctx, params: CallToolRequestParams) -> CallToolResult:
        # arguments es opcional en el protocolo; los dispatchers esperan un dict.
        content = await dispatch(params.name, params.arguments or {})
        return CallToolResult(content=list(content))

    srv.add_request_handler("tools/list", PaginatedRequestParams, _list)
    srv.add_request_handler("tools/call", CallToolRequestParams, _call)


# Client-facing error text. Detail is logged server-side, never returned — the
# public transport must not leak internal paths, SQL, or library internals.
GENERIC_ERROR = "Error interno procesando la solicitud."


async def dispatch_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute a tool by name. Shared by the stdio and public HTTP transports."""
    # Log argument KEYS only — values may carry PII on now-public traffic.
    log.info("call_tool: %s (args: %s)", name, sorted(arguments))

    if name == "search_docs":
        from src.vectorstore.pg_retriever import hybrid_search
        from src.vectorstore.pg_collections import collection_counts

        q = arguments.get("query", "")
        col = arguments.get("collection", "gx_docs")
        k = arguments.get("top_k", 5)
        snippet_chars = arguments.get("snippet_chars", 600)
        min_score = arguments.get("min_score")
        try:
            results = hybrid_search(q, collection=col, top_k=k, min_score=min_score)
            if not results:
                # Guide the caller to a collection that actually has content.
                populated = []
                try:
                    populated = [f"{n} ({c} chunks)" for n, c in collection_counts()]
                except Exception:
                    pass
                hint = (
                    f" Colecciones con contenido: {', '.join(populated)}."
                    if populated
                    else " No hay colecciones con contenido; ingestá con add_from_url."
                )
                return [
                    TextContent(type="text", text=f"Sin resultados en '{col}'.{hint}")
                ]
            lines = [f"## Resultados ({len(results)} found)\n"]
            for r in results:
                content = r["content"] or ""
                snippet = content[:snippet_chars]
                truncated = "…" if len(content) > snippet_chars else ""
                lines.append(
                    f"**{r['title']}** (score={r['score']:.3f})\n"
                    f"doc_id: `{r['doc_id']}`\n"
                    f"article_id: `{r.get('id', '')}`\n"
                    f"Fuente: {r.get('url') or r.get('source', '?')}\n"
                    f"Hash: {r.get('content_hash', '')}\n"
                    f"{snippet}{truncated}\n"
                )
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception:
            log.exception("search_docs error")
            return [TextContent(type="text", text=GENERIC_ERROR)]

    elif name == "get_document":
        from src.vectorstore.pg_collections import get_document

        doc_id = arguments.get("doc_id", "")
        collection = arguments.get("collection", "gx_docs")
        try:
            doc = get_document(doc_id, collection=collection)
            if not doc:
                return [TextContent(type="text", text="Documento no encontrado.")]
            return [
                TextContent(
                    type="text",
                    text=f"# {doc['title']}\n\n"
                    f"**Fuente:** {doc.get('source', '')}\n"
                    f"**Colección:** {doc.get('collection', '')}\n"
                    f"**Hash:** {doc.get('content_hash', '')}\n\n"
                    f"{doc.get('content', '')}",
                )
            ]
        except Exception:
            log.exception("%s failed", name)
            return [TextContent(type="text", text=GENERIC_ERROR)]

    elif name == "get_article":
        from src.vectorstore.pg_collections import get_article

        article_id = arguments.get("article_id", "")
        collection = arguments.get("collection", "gx_docs")
        try:
            art = get_article(article_id, collection=collection)
            if not art:
                return [TextContent(type="text", text="Artículo no encontrado.")]
            return [
                TextContent(
                    type="text",
                    text=f"# {art['title']} ({art['chunks']} chunks)\n\n"
                    f"**Fuente:** {art.get('url') or art.get('source', '')}\n"
                    f"**Colección:** {art.get('collection', '')}\n"
                    f"**Hash:** {art.get('content_hash', '')}\n\n"
                    f"{art.get('content', '')}",
                )
            ]
        except Exception:
            log.exception("%s failed", name)
            return [TextContent(type="text", text=GENERIC_ERROR)]

    elif name == "verify_syntax":
        from src.syntax_db.gx_repository import verify_syntax

        snippet = arguments.get("snippet", "")
        lang = arguments.get("language", "gx")
        version = arguments.get("version")
        try:
            verdict = verify_syntax(snippet, language=lang, version=version)
            return [TextContent(type="text", text=str(verdict))]
        except Exception:
            log.exception("%s failed", name)
            return [TextContent(type="text", text=GENERIC_ERROR)]

    elif name == "list_methods":
        from src.syntax_db.gx_repository import list_methods

        obj_type = arguments.get("object_type", "")
        version = arguments.get("version")
        try:
            methods = list_methods(obj_type, version=version)
            if not methods:
                return [TextContent(type="text", text="Sin resultados.")]
            return [
                TextContent(
                    type="text",
                    text="Métodos conocidos:\n" + "\n".join(f"- {m}" for m in methods),
                )
            ]
        except Exception:
            log.exception("%s failed", name)
            return [TextContent(type="text", text=GENERIC_ERROR)]

    elif name == "add_from_url":
        from scripts.scrape_wiki import scrape
        from src.ingestion.ingestor import ingest

        url = arguments.get("url", "")
        if not url:
            return [TextContent(type="text", text="URL requerida.")]
        try:
            article = scrape(url)
            result = ingest(article)
            return [
                TextContent(
                    type="text",
                    text=f"Scraped: {result['title']}\n"
                    f"Género: {result['genre']}\n"
                    f"Colección: {result['destination']}\n"
                    f"Chunks indexados: {result['chunks_indexed']}\n"
                    f"Hash: {result['content_hash'][:16]}...\n"
                    f"Palabras: {result['word_count']}\n"
                    f"Saved: {result['md_path']}",
                )
            ]
        except Exception:
            log.exception("add_from_url error")
            return [TextContent(type="text", text=GENERIC_ERROR)]

    elif name == "ingest_batch":
        return [
            TextContent(
                type="text",
                text="Batch ingestion aún no implementado. "
                "Usar add_from_url para artículos individuales.",
            )
        ]

    else:
        return [TextContent(type="text", text=f"Tool '{name}' no implementada.")]


# El cableado va aca, despues de dispatch_tool: register_tools recibe la funcion
# ya definida. Con los decoradores esto quedaba arriba porque el cuerpo del
# handler resolvia el nombre recien al invocarse.
register_tools(server, [*READ_TOOLS, *WRITE_TOOLS], dispatch_tool)


# ─── Entry point ──────────────────────────────────────────────────────────────


async def main():
    log.info(f"{APP_NAME} v{VERSION} starting (stdio)...")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
