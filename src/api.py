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
    page_id: str | None
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
    Estado del scraper: artículos guardados, colecciones indexadas, espacio en disco.
    """
    files = list(SCRAPED_DIR.glob("*.md")) if SCRAPED_DIR.exists() else []
    total_size = sum(f.stat().st_size for f in files)

    # collection_counts() y no list_collections(): trae los nombres Y los conteos
    # en una sola query (GROUP BY), y el tamaño del corpus es justo lo que la UI
    # de curación necesita mostrar. Con dos llamadas eran dos consultas para la
    # misma información.
    from src.vectorstore.pg_collections import collection_counts

    chunk_counts: dict[str, int] = {}
    store_error = None
    try:
        chunk_counts = dict(collection_counts())
    except Exception as exc:
        store_error = str(exc)
        log.warning(f"/status: failed to count collections: {exc}")

    stat = shutil.disk_usage(".")
    return {
        "scraped_articles": len(files),
        "collections": sorted(chunk_counts),
        # Chunks indexados por colección, y el total. `store_error` distingue
        # "el store no tiene nada" de "no se pudo consultar el store": sin eso, un
        # Postgres caído se ve igual que un corpus vacío.
        "chunk_counts": chunk_counts,
        "total_chunks": sum(chunk_counts.values()),
        "store_error": store_error,
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
    Búsqueda híbrida (pgvector + tsvector). Proxy para MCP wrapper.
    """
    from src.vectorstore.pg_retriever import hybrid_search

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
    from src.vectorstore.pg_collections import get_document

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
    from src.vectorstore.pg_collections import get_article

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

# ─── Discovery endpoint ────────────────────────────────────────────────────────
import re as _re
import httpx as _httpx
from bs4 import BeautifulSoup as _BeautifulSoup


@app.get("/discover", tags=["discovery"])
def discover(
    source: Annotated[str, Query(description="Fuente: wiki | sac")] = "wiki",
    query: Annotated[str, Query(description="Tema o query a buscar")] = "",
    max_urls: Annotated[int, Query(description="Máximo de URLs", ge=1, le=100)] = 50,
):
    """
    Descubre URLs de artículos en wiki.genexus.com o sac.genexus.com por tema.
    Usa httpx interno (el contenedor puede alcanzar la wiki).
    """
    if not query:
        raise HTTPException(status_code=400, detail="query es requerido")

    log.info(f"/discover source={source} query={query}")

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; gx-bt-rag-mcp/0.1)",
        "Accept": "text/html",
    }

    if source == "wiki":
        fetch_url = f"https://wiki.genexus.com/广场/wiki?{query}"
    elif source == "sac":
        fetch_url = f"https://sac.genexus.com/frontend.search.aspx?q={query}"
    else:
        raise HTTPException(status_code=400, detail="source debe ser 'wiki' o 'sac'")

    try:
        with _httpx.Client(timeout=30.0) as client:
            resp = client.get(fetch_url, headers=headers, follow_redirects=True)
            resp.raise_for_status()
    except Exception as exc:
        log.error(f"/discover: falló fetch {fetch_url}: {exc}")
        raise HTTPException(status_code=502, detail=f"No se pudo acceder a {source}: {exc}")

    soup = _BeautifulSoup(resp.text, "html.parser")
    urls = []
    seen = set()

    if source == "wiki":
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "servlet/wiki" not in href and "wiki.genexus.com" not in href:
                continue
            full = href if href.startswith("http") else f"https://wiki.genexus.com{href}"
            nums = _re.findall(r"\?(\d+)", full)
            if nums and full not in seen:
                seen.add(full)
                title = a.get_text(strip=True) or f"Article {nums[0]}"
                urls.append({"url": full, "title": title, "page_id": nums[0]})

    elif source == "sac":
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "scrmain" not in href:
                continue
            full = href if href.startswith("http") else f"https://sac.genexus.com/{href}"
            nums = _re.findall(r",(\d+);", full)
            if full not in seen:
                seen.add(full)
                title = a.get_text(strip=True) or "SAC Article"
                article_id = nums[0] if nums else "unknown"
                urls.append({"url": full, "title": title, "article_id": article_id})

    urls = urls[:max_urls]
    log.info(f"/discover: {len(urls)} URLs para '{query}' en {source}")
    return {"source": source, "query": query, "count": len(urls), "urls": urls}
