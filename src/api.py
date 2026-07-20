"""
gx_rag_api — API HTTP para ingesta de artículos wiki GeneXus.

Puerto: 8000
Expone endpoints para scraper, ver estado y gestionar la cola de ingesta.
"""

import json
import logging
import shutil
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl

from scripts import scrape_wiki as scraper
from src.ingestion.ingestor import ingest, SCRAPED_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BYTES_PER_GB = 1024**3

app = FastAPI(
    title="gx-bt-rag-mcp Ingestor API",
    description="API de ingesta de artículos wiki GeneXus para el servidor MCP de retrieval.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """
    Central handler for uncaught errors. Logs the failing route and returns a
    uniform 500. Endpoints stay thin: they let unexpected errors propagate here
    instead of repeating try/except/log/raise. HTTPException is handled upstream
    by Starlette, so 4xx responses are unaffected.
    """
    log.error(f"{request.method} {request.url.path} failed: {exc}")
    return JSONResponse(status_code=500, content={"detail": str(exc)})


# ─── Models ───────────────────────────────────────────────────────────────────


class ScrapeResponse(BaseModel):
    url: str
    title: str
    page_id: str
    genre: str
    destination: str
    source_key: str
    content_hash: str
    version_hint: str | None
    word_count: int
    markdown_path: str
    metadata_path: str
    chunks_indexed: int = 0


class ScrapeRequest(BaseModel):
    url: HttpUrl


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class ArticleItem(BaseModel):
    url: str
    title: str
    page_id: str | None
    genre: str
    destination: str
    source_key: str
    content_hash: str
    version_hint: str | None
    word_count: int
    scraped_at: str | None


class ArticlesResponse(BaseModel):
    count: int
    articles: list[ArticleItem]


# ─── Endpoints ────────────────────────────────────────────────────────────────


@app.get("/", tags=["root"])
def root():
    return {
        "service": "gx-bt-rag-mcp Ingestor API",
        "version": "0.1.0",
        "endpoints": {
            "health": "GET /health",
            "status": "GET /status",
            "scrape": "POST /scrape — { url: string }",
            "scrape_get": "GET /scrape?url=<url>",
        },
        "docs": "/docs",
    }


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health():
    return HealthResponse(status="ok", service="gx-rag-ingestor", version="0.1.0")


@app.post("/scrape", response_model=ScrapeResponse, tags=["ingestion"])
def scrape_article(req: ScrapeRequest):
    """
    Scrapea e ingiere un artículo: guarda en filesystem + indexa en ChromaDB.
    """
    url = str(req.url)
    log.info(f"/scrape request: {url}")

    article = scraper.scrape(url)
    result = ingest(article)

    meta = article.metadata
    return ScrapeResponse(
        url=meta.url,
        title=meta.title,
        page_id=meta.page_id,
        genre=meta.genre,
        destination=meta.destination,
        source_key=meta.source_key,
        content_hash=meta.content_hash,
        version_hint=meta.version_hint,
        word_count=meta.word_count,
        markdown_path=result["md_path"],
        metadata_path=result["meta_path"],
        chunks_indexed=result["chunks_indexed"],
    )


@app.get("/scrape", tags=["ingestion"])
def scrape_get(url: Annotated[str, Query(description="URL del artículo wiki")] = ""):
    """
    Scrape GET para pruebas rápidas en navegador.
    """
    if not url:
        raise HTTPException(status_code=400, detail="url query param required")
    return scrape_article(ScrapeRequest(url=url))


@app.get("/status", tags=["ingestion"])
def status():
    """
    Estado del scraper: artículos guardados, chunks en ChromaDB, espacio en disco.
    """
    files = list(SCRAPED_DIR.glob("*.md")) if SCRAPED_DIR.exists() else []
    total_size = sum(f.stat().st_size for f in files)

    from src.vectorstore.chroma_client import get_client

    collections = []
    chroma_error = None
    try:
        collections = [c.name for c in get_client().list_collections()]
    except Exception as exc:
        chroma_error = str(exc)
        log.warning(f"/status: failed to list ChromaDB collections: {exc}")

    stat = shutil.disk_usage(".")
    return {
        "scraped_articles": len(files),
        "chroma_collections": collections,
        "chroma_error": chroma_error,
        "total_size_bytes": total_size,
        "disk_free_gb": round(stat.free / BYTES_PER_GB, 1),
    }


@app.get("/articles", response_model=ArticlesResponse, tags=["ingestion"])
def list_articles():
    """
    Lista todos los artículos scrapeados con su metadata.
    """
    articles = []
    for meta_file in SCRAPED_DIR.glob("*.meta.json"):
        try:
            with open(meta_file, encoding="utf-8") as f:
                data = json.load(f)
            articles.append(
                ArticleItem(
                    url=data.get("url", ""),
                    title=data.get("title", "Unknown"),
                    page_id=data.get("page_id"),
                    genre=data.get("genre", "unknown"),
                    destination=data.get("destination", ""),
                    source_key=data.get("source_key", ""),
                    content_hash=data.get("content_hash", ""),
                    version_hint=data.get("version_hint"),
                    word_count=data.get("word_count", 0),
                    scraped_at=data.get("scraped_at"),
                )
            )
        except Exception as exc:
            log.warning(f"Failed to read {meta_file}: {exc}")
    articles.sort(key=lambda a: a.scraped_at or "", reverse=True)
    return ArticlesResponse(count=len(articles), articles=articles)


# ─── Retrieval endpoints (MCP wrapper support) ─────────────────────────────────


@app.get("/search", tags=["retrieval"])
def search(
    q: Annotated[str, Query(description="Query")],
    collection: Annotated[str, Query(description="Colección")] = "gx_docs",
    top_k: Annotated[int, Query(description="Resultados", ge=1, le=20)] = 5,
):
    """
    Búsqueda híbrida en ChromaDB. Proxy para MCP wrapper.
    """
    from src.vectorstore.retriever import hybrid_search

    results = hybrid_search(q, collection=collection, top_k=top_k)
    return {"results": results, "count": len(results)}


@app.get("/documents", tags=["retrieval"])
def get_document(
    doc_id: Annotated[str, Query(alias="id", description="Document ID")],
    collection: Annotated[str, Query(description="Colección")] = "gx_docs",
):
    """
    Obtiene contenido completo de un documento por ID.
    """
    from src.vectorstore.collections import get_document

    doc = get_document(doc_id, collection=collection)
    if not doc:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    return doc


@app.get("/article", tags=["retrieval"])
def get_article(
    article_id: Annotated[str, Query(alias="id", description="Article ID")],
    collection: Annotated[str, Query(description="Colección")] = "gx_docs",
):
    """
    Reensambla un artículo completo a partir de sus chunks (orden chunk_index).
    """
    from src.vectorstore.collections import get_article

    art = get_article(article_id, collection=collection)
    if not art:
        raise HTTPException(status_code=404, detail="Artículo no encontrado")
    return art


@app.get("/verify", tags=["syntax"])
def verify_syntax(
    snippet: Annotated[str, Query(description="Snippet a verificar")],
    language: Annotated[str, Query(description="gx | bt_api")] = "gx",
    version: Annotated[str, Query(description="Versión gx16|gx18")] = "",
):
    """
    Verifica si un snippet es sintácticamente válido.
    """
    from src.syntax_db.gx_repository import verify_syntax

    result = verify_syntax(snippet, language=language, version=version or None)
    return {"result": str(result)}


@app.get("/methods", tags=["syntax"])
def list_methods(
    object_type: Annotated[str, Query(description="Tipo de objeto GeneXus")],
    version: Annotated[str, Query(description="Versión gx16|gx18")] = "",
):
    """
    Lista métodos/propiedades de un objeto GeneXus.
    """
    from src.syntax_db.gx_repository import list_methods

    methods = list_methods(object_type, version=version or None)
    return {"methods": methods, "count": len(methods)}
