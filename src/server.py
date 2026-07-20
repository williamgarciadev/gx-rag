"""
gx_bt_rag.server — MCP server (stdio transport).

Retrieval semántico (ChromaDB + hybrid search) sobre documentación
GeneXus y Bantotal, con capa de validación contra SQLite.
"""

import sys
import logging
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

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

@server.list_tools()
async def list_tools() -> list[Tool]:
    """Define las tools disponibles para el cliente MCP."""
    return [
        Tool(
            name="search_docs",
            description=(
                "Búsqueda híbrida en la documentación GeneXus/Bantotal. "
                "Busca en las colecciones gx_docs y bt_docs. "
                "Devuelve los fragmentos más relevantes con score."
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
                },
                "required": ["query"],
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
        Tool(
            name="add_from_url",
            description=(
                "Ingiere un artículo de la wiki GeneXus desde su URL. "
                "Lo scarpea, clasifica, curade y almacena en ChromaDB."
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


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Ejecuta una tool y devuelve el resultado."""
    log.info(f"call_tool: {name} {arguments}")

    # ─── Lazy imports para no cargar todo al levantar ──────────────────────
    if name == "search_docs":
        from src.vectorstore.retriever import hybrid_search
        q = arguments.get("query", "")
        col = arguments.get("collection", "gx_docs")
        k = arguments.get("top_k", 5)
        try:
            results = hybrid_search(q, collection=col, top_k=k)
            if not results:
                return [TextContent(type="text", text="No se encontraron resultados.")]
            lines = [f"## Resultados ({len(results)} found)\n"]
            for r in results:
                lines.append(
                    f"**{r['title']}** (score={r['score']:.3f})\n"
                    f"Fuente: {r.get('source', '?')}\n"
                    f"{r['content'][:300]}...\n"
                )
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception as exc:
            log.error(f"search_docs error: {exc}")
            return [TextContent(type="text", text=f"Error en búsqueda: {exc}")]

    elif name == "get_document":
        from src.vectorstore.collections import get_document
        doc_id = arguments.get("doc_id", "")
        collection = arguments.get("collection", "gx_docs")
        try:
            doc = get_document(doc_id, collection=collection)
            if not doc:
                return [TextContent(type="text", text="Documento no encontrado.")]
            return [TextContent(
                type="text",
                text=f"# {doc['title']}\n\n"
                     f"**Fuente:** {doc.get('source', '')}\n"
                     f"**Colección:** {doc.get('collection', '')}\n"
                     f"**Hash:** {doc.get('content_hash', '')}\n\n"
                     f"{doc.get('content', '')}",
            )]
        except Exception as exc:
            return [TextContent(type="text", text=f"Error: {exc}")]

    elif name == "verify_syntax":
        from src.syntax_db.gx_repository import verify_syntax
        snippet = arguments.get("snippet", "")
        lang = arguments.get("language", "gx")
        version = arguments.get("version")
        try:
            verdict = verify_syntax(snippet, language=lang, version=version)
            return [TextContent(type="text", text=str(verdict))]
        except Exception as exc:
            return [TextContent(type="text", text=f"Error: {exc}")]

    elif name == "list_methods":
        from src.syntax_db.gx_repository import list_methods
        obj_type = arguments.get("object_type", "")
        version = arguments.get("version")
        try:
            methods = list_methods(obj_type, version=version)
            if not methods:
                return [TextContent(type="text", text="Sin resultados.")]
            return [TextContent(
                type="text",
                text="Métodos conocidos:\n" + "\n".join(f"- {m}" for m in methods),
            )]
        except Exception as exc:
            return [TextContent(type="text", text=f"Error: {exc}")]

    elif name == "add_from_url":
        from scripts.scrape_wiki import scrape
        from src.vectorstore.indexer import index_article
        url = arguments.get("url", "")
        collection = arguments.get("collection")
        if not url:
            return [TextContent(type="text", text="URL requerida.")]
        try:
            article = scrape(url)
            meta = article.metadata
            dest = collection or meta.destination

            # Indexar en ChromaDB
            chunk_ids = index_article(
                article_id=meta.source_key,
                title=meta.title,
                content=article.markdown,
                source=url,
                genre=meta.genre,
                url=url,
                content_hash=meta.content_hash,
                collection=dest,
            )

            return [TextContent(
                type="text",
                text=f"Scraped: {meta.title}\n"
                     f"Género: {meta.genre}\n"
                     f"Colección: {dest}\n"
                     f"Chunks indexados: {len(chunk_ids)}\n"
                     f"Hash: {meta.content_hash[:16]}...\n"
                     f"Palabras: {meta.word_count}",
            )]
        except Exception as exc:
            log.error(f"add_from_url error: {exc}")
            return [TextContent(type="text", text=f"Error: {exc}")]

    elif name == "ingest_batch":
        # TODO: implementar batch
        return [TextContent(
            type="text",
            text="Batch ingestion aún no implementado. "
                 "Usar add_from_url para artículos individuales.",
        )]

    else:
        return [TextContent(type="text", text=f"Tool '{name}' no implementada.")]


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
