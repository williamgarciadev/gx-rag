"""
Módulo de ingesta compartido — guarda artículos scrapeados en el filesystem
y los indexa en ChromaDB. Usado por la API HTTP y por el servidor MCP.
"""

import json
import logging
from dataclasses import asdict
from pathlib import Path

from scripts.scrape_wiki import ScrapedArticle
from src.paths import SCRAPED_DIR

log = logging.getLogger(__name__)

SCRAPED_DIR.mkdir(parents=True, exist_ok=True)


def save_article(article: ScrapedArticle, name: str | None = None) -> tuple[Path, Path]:
    """
    Guarda el artículo scrapeado como .md + .meta.json en data/scraped/.
    `name` fuerza el nombre base del archivo (p. ej. "sac_61036"); si no se
    pasa, se usa page_id o los primeros 12 chars del content_hash.
    Devuelve (md_path, meta_path).
    """
    meta = article.metadata
    safe_name = name or meta.page_id or meta.content_hash[:12]
    base = SCRAPED_DIR / safe_name

    md_path = base.with_suffix(".md")
    md_path.write_text(article.markdown, encoding="utf-8")

    meta_path = base.with_suffix(".meta.json")
    meta_dict = {k: v for k, v in asdict(meta).items() if v is not None}
    meta_path.write_text(
        json.dumps(meta_dict, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    log.info(f"Saved article: {md_path} ({meta.word_count} words)")
    return md_path, meta_path


def index_article(article: ScrapedArticle) -> list[str]:
    """
    Indexa el artículo en ChromaDB.
    Devuelve lista de chunk IDs insertados.
    """
    from src.vectorstore.pg_indexer import index_article as _index
    meta = article.metadata

    chunk_ids = _index(
        article_id=meta.source_key,
        title=meta.title,
        content=article.markdown,
        source=meta.url,
        genre=meta.genre,
        url=meta.url,
        content_hash=meta.content_hash,
        collection=meta.destination,
    )

    log.info(f"Indexed {len(chunk_ids)} chunks for {meta.source_key}")
    return chunk_ids


def ingest(article: ScrapedArticle) -> dict:
    """
    Flujo completo de ingesta: save filesystem + index ChromaDB.
    Devuelve resumen con paths y chunks.
    """
    md_path, meta_path = save_article(article)
    chunk_ids = index_article(article)

    return {
        "title": article.metadata.title,
        "genre": article.metadata.genre,
        "destination": article.metadata.destination,
        "content_hash": article.metadata.content_hash,
        "word_count": article.metadata.word_count,
        "md_path": str(md_path),
        "meta_path": str(meta_path),
        "chunks_indexed": len(chunk_ids),
    }
