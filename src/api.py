"""
gx_rag_api — API HTTP para ingesta de artículos wiki GeneXus.

Puerto: 8000
Expone endpoints para scraper, ver estado y gestionar la cola de ingesta.
"""

import logging
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

from scripts import scrape_wiki as scraper
from src.ingestion.ingestor import ingest, SCRAPED_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

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

    try:
        article = scraper.scrape(url)
        result = ingest(article)
    except Exception as exc:
        log.error(f"Scrape failed for {url}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

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
    import os, shutil
    files = list(SCRAPED_DIR.glob("*.md")) if SCRAPED_DIR.exists() else []
    total_size = sum(f.stat().st_size for f in files)

    from src.vectorstore.chroma_client import get_client
    collections = []
    try:
        collections = [c.name for c in get_client().list_collections()]
    except Exception:
        pass

    stat = shutil.disk_usage(".")
    return {
        "scraped_articles": len(files),
        "chroma_collections": collections,
        "total_size_bytes": total_size,
        "disk_free_gb": round(stat.free / (1024**3), 1),
    }


@app.get("/articles", response_model=ArticlesResponse, tags=["ingestion"])
def list_articles():
    """
    Lista todos los artículos scrapeados con su metadata.
    """
    import json
    articles = []
    for meta_file in SCRAPED_DIR.glob("*.meta.json"):
        try:
            with open(meta_file, encoding="utf-8") as f:
                data = json.load(f)
            articles.append(ArticleItem(
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
            ))
        except Exception as exc:
            log.warning(f"Failed to read {meta_file}: {exc}")
    articles.sort(key=lambda a: a.scraped_at or "", reverse=True)
    return ArticlesResponse(count=len(articles), articles=articles)
