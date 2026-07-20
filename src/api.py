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


class ScrapeRequest(BaseModel):
    url: HttpUrl


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


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
    Scrapea un artículo de la wiki GeneXus desde su URL.
    Devuelve la metadata y guarda el Markdown + JSON en data/scraped/.
    """
    url = str(req.url)
    log.info(f"/scrape request: {url}")

    try:
        article = scraper.scrape(url)
        meta = article.metadata
    except Exception as exc:
        log.error(f"Scrape failed for {url}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    # Guardar
    out_dir = Path("data/scraped")
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = meta.page_id or meta.content_hash[:12]
    base = out_dir / safe_name

    md_path = base.with_suffix(".md")
    md_path.write_text(article.markdown, encoding="utf-8")

    meta_path = base.with_suffix(".meta.json")
    import json
    meta_path.write_text(
        json.dumps({
            "url": meta.url,
            "title": meta.title,
            "page_id": meta.page_id,
            "genre": meta.genre,
            "destination": meta.destination,
            "source_key": meta.source_key,
            "content_hash": meta.content_hash,
            "version_hint": meta.version_hint,
            "word_count": meta.word_count,
            "scraped_at": meta.scraped_at,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

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
        markdown_path=str(md_path),
        metadata_path=str(meta_path),
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
    Estado del scraper: artículos guardados, espacio en disco.
    """
    import os, shutil
    scraped_dir = Path("data/scraped")
    files = list(scraped_dir.glob("*.md")) if scraped_dir.exists() else []
    total_size = sum(f.stat().st_size for f in files)

    stat = shutil.disk_usage(".")
    return {
        "scraped_articles": len(files),
        "total_size_bytes": total_size,
        "disk_free_gb": round(stat.free / (1024**3), 1),
    }
