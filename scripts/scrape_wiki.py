#!/usr/bin/env python3
"""
gx_wiki_scraper — scraper de artículos de la wiki GeneXus.

Funcionalidad:
- Descarga un artículo desde docs.genexus.com o wiki.genexus.com
- Extrae el contenido principal (sin navegación/UI)
- Clasifica por género: referencia (→ catálogo) o guía (→ retrieval)
- Produce Markdown limpio con procedencia
- Calcula source_key y content_hash para idempotencia

Uso:
    python scripts/scrape_wiki.py <url> [--output-dir <dir>]
"""

import argparse
import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
import html2text

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ArticleMetadata:
    url: str
    title: str
    page_id: str | None
    genre: str  # "reference" | "guide"
    destination: str  # "gx_syntax" | "gx_docs"
    source_key: str  # normalized URL (domain-unified)
    content_hash: str  # SHA256 of normalized raw text
    version_hint: str | None
    word_count: int
    scraped_at: str


@dataclass
class ScrapedArticle:
    metadata: ArticleMetadata
    markdown: str
    raw_text: str


# ─────────────────────────────────────────────────────────────────────────────
# URL normalization
# ─────────────────────────────────────────────────────────────────────────────


def normalize_wiki_url(url: str) -> str:
    """
    Normaliza URLs de docs.genexus.com y wiki.genexus.com a una clave canónica.
    Los dos dominios son la misma documentación; se unifican.
    La source_key usa solo el pageId numérico (sin slug).
    """
    parsed = httpx.URL(url)
    host = parsed.host.lower() if parsed.host else ""
    if host in ("docs.genexus.com", "wiki.genexus.com"):
        canonical_host = "docs.genexus.com"
    else:
        canonical_host = host

    query = parsed.params
    if query:
        raw_key = list(query.keys())[0] if query else ""
        # Extraer solo el ID numérico
        numeric_id = raw_key.split(",")[0] if "," in raw_key else raw_key
        if numeric_id.isdigit():
            return f"https://{canonical_host}/en/wiki?{numeric_id}"

    path = parsed.path.rstrip("/")
    return f"https://{canonical_host}{path}"


def extract_page_id(url: str) -> str | None:
    """Extrae el pageId numérico de la URL wiki (solo el número)."""
    parsed = httpx.URL(url)
    query = parsed.params
    if query:
        key = list(query.keys())[0]
        # La key es "24744,For each command" — separar solo el numérico
        numeric = key.split(",")[0] if "," in key else key
        return numeric if numeric.isdigit() else None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Genre classification
# ─────────────────────────────────────────────────────────────────────────────

_REF_INDICATORS = [
    "syntax",
    "command",
    "property",
    "method",
    "object",
    "attribute",
    "clause",
    "function",
    "operator",
    "event",
]
_GUIDE_INDICATORS = [
    "how to",
    "howto:",
    "tutorial",
    "guide",
    "example",
    "best practice",
    "optimization",
    "performance",
    "tip",
    "trick",
    "considerations",
    "walkthrough",
    "step by step",
]
_TUTORIAL_PATTERNS = [
    "step 1",
    "step 2",
    "step 3",
    "first, ",
    "adding ",
    "## summary",
    "## the challenge",
    "the following steps",
]
_STOPWORDS_TITLE = ["category", "table of contents", "index", "home"]


def classify_genre(title: str, text: str, url: str) -> tuple[str, str]:
    """
    Clasifica un artículo como 'reference' o 'guide'.
    Retorna (genre, destination).
    reference → gx_syntax (catálogo)
    guide     → gx_docs   (retrieval)
    """
    tl = title.lower()
    ul = url.lower()
    ts = text[:600].lower()

    def score(words):
        return sum(1 for w in words if w in ts)

    ref = score(_REF_INDICATORS)
    guide = score(_GUIDE_INDICATORS)

    # Bonus por indicadores fuertes en título
    if any(t in tl for t in ["syntax", "command", "clause"]):
        ref += 5
    if any(t in tl for t in _STOPWORDS_TITLE):
        return "guide", "gx_docs"
    if any(
        t in tl for t in ["how to", "howto:", "tutorial", "example", "optimization"]
    ):
        guide += 3

    # Patrones de tutorial en el cuerpo (mayor peso: son indicador fuerte)
    for pat in ["step ", "adding ", "challenge"]:
        count = ts.lower().count(pat)
        if count >= 1:
            guide += count * 2  # peso 2 por repetición

    if ref > guide:
        return "reference", "gx_syntax"
    return "guide", "gx_docs"


# ─────────────────────────────────────────────────────────────────────────────
# HTML extraction
# ─────────────────────────────────────────────────────────────────────────────


def fetch_article(url: str, timeout: float = 45.0) -> httpx.Response:
    """Descarga un artículo wiki."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; gx-bt-rag-mcp/0.1)",
        "Accept": "text/html,application/xhtml+xml",
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, headers=headers, follow_redirects=True)
        resp.raise_for_status()
        return resp


def _decompose_safe(element: BeautifulSoup, tag) -> None:
    """Decompose solo si el tag aún existe en el documento."""
    if tag.parent:
        tag.decompose()


# SAC (sac.genexus.com) noise: navigation chrome and hidden conditional
# messages that pollute the article body. Exact ids are page-level (stable);
# suffixes match the per-instance GeneXus web-component ids (W####<SUFFIX>).
_SAC_NOISE_EXACT_IDS = ("TABLE6", "TABLAERRORES", "TABLACONTENIDO")
_SAC_NOISE_ID_SUFFIXES = (
    "LOGINTXT",  # "ingresando con tu CUENTA GeneXus" login nudge
    "VOLVERALISTA",  # "VOLVER A LA LISTA"
    "ERRORLNG",  # hidden "No hay traduccion"
    "ERRORNOTEXIST",  # hidden "La busqueda no dio resultados"
    "ERRORACCESSLVL",  # hidden "El SAC no se encuentra disponible"
)


def _clean_sac_content(contenido: BeautifulSoup) -> BeautifulSoup:
    """Strip nav chrome and hidden messages from a SAC td#CONTENIDO block.

    Removes the "Recuerda que … CUENTA GeneXus" login nudge, the
    PRIMERA/ANTERIOR/SIGUIENTE/LAST pager, "VOLVER A LA LISTA", and the hidden
    GeneXus conditional error divs. Keeps the report date, the "SAC #NNNNN"
    title, Abstract, Descripción Detallada, Work Around and Productos.
    """
    for tag in contenido.find_all(["script", "style", "noscript", "iframe", "svg"]):
        _decompose_safe(contenido, tag)

    for eid in _SAC_NOISE_EXACT_IDS:
        for tag in contenido.find_all(attrs={"id": eid}):
            _decompose_safe(contenido, tag)

    for tag in contenido.find_all(
        attrs={"id": lambda x: x and str(x).endswith(_SAC_NOISE_ID_SUFFIXES)}
    ):
        _decompose_safe(contenido, tag)

    return contenido


def extract_main_content(soup: BeautifulSoup) -> BeautifulSoup:
    """
    Extrae el contenido principal del artículo wiki o SAC.

    SAC:  td#CONTENIDO (cuerpo real del artículo, no el wrapper de filtros)
    Wiki: gx-content-placeholder → MAINTABLE → body

    Se eliminan solo los elementos ruidosos específicos, nunca contenedores padre.
    """
    # SAC: el artículo real vive en td#CONTENIDO
    contenido = soup.find("td", {"id": "CONTENIDO"})
    if contenido:
        return _clean_sac_content(contenido)

    # gx-content-placeholder contiene el cuerpo del artículo
    article = soup.find("div", {"class": "gx-content-placeholder"})
    if not article:
        article = soup.find("div", {"id": "MAINTABLE"})
    if not article:
        article = soup.body
    if not article:
        return soup

    # Eliminar ruido hijos del artículo
    for tag in article.find_all(["script", "style", "noscript", "iframe", "svg"]):
        _decompose_safe(article, tag)

    # Eliminar los divs de navegación específicos
    for nid in [
        "TABLEFEEDBACK_MPAGE",
        "NEXTNODE_MPAGE",
        "TABLERECENTLINKS_MPAGE",
        "TABLEINFO_MPAGE",
    ]:
        for tag in article.find_all(attrs={"id": lambda x: x and nid in str(x)}):
            _decompose_safe(article, tag)

    return article


def extract_version_hint(soup: BeautifulSoup) -> str | None:
    """
    Extrae la versión GeneXus del artículo.
    Busca en la sección 'This documentation is valid for:' del artículo.
    """
    # Buscar el div que contiene las versiones válidas
    article = soup.find("div", {"class": "gx-content-placeholder"}) or soup
    # Buscar el texto 'GeneXus XX Help' dentro del contenido del artículo
    text = article.get_text()
    # Capturar todas las versiones mencionadas (pueden ser varias)
    versions = re.findall(r"(GeneXus\s+[\d]+(?:\s+\w+)?\s+Help)", text)
    if versions:
        # Devolver la más reciente (primera en la lista)
        return versions[0].strip()
    return None


def html_to_markdown(html_content: str) -> str:
    """Convierte HTML a Markdown."""
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = False
    h.body_width = 0
    h.unicode_snob = True
    h.skip_internal_links = True
    return h.handle(html_content)


# ─────────────────────────────────────────────────────────────────────────────
# Content hashing
# ─────────────────────────────────────────────────────────────────────────────


def compute_hash(text: str) -> str:
    """SHA256 del texto normalizado."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Main scraper
# ─────────────────────────────────────────────────────────────────────────────


def scrape(url: str) -> ScrapedArticle:
    """Scrape completo de un artículo wiki."""
    log.info(f"Scraping: {url}")

    resp = fetch_article(url)
    soup = BeautifulSoup(resp.text, "html.parser")

    # Extraer versión (antes de perder el soup original)
    version_hint = extract_version_hint(soup)

    # Extraer y limpiar contenido primero (para obtener raw_text como fallback del título)
    content_soup = extract_main_content(soup)
    raw_text = content_soup.get_text(separator="\n", strip=True)

    # Extraer título: SAC (*SACTIT) → H1.WikiName → primer renglón → "Unknown"
    sac_title = content_soup.find(id=lambda x: x and str(x).endswith("SACTIT"))
    h1 = soup.find("h1", {"class": "WikiName"})
    if sac_title and sac_title.get_text(strip=True):
        title = sac_title.get_text(strip=True)
    elif h1:
        title = h1.get_text(strip=True)
    elif raw_text:
        title = raw_text.split("\n")[0].strip()
        if len(title) > 200:  # si es demasiado largo, acortar
            title = title[:200]
    else:
        title = "Unknown"

    # Clasificar género
    genre, destination = classify_genre(title, raw_text, url)

    # Normalizar source_key
    source_key = normalize_wiki_url(url)

    # Hash
    content_hash = compute_hash(raw_text)

    page_id = extract_page_id(url)

    metadata = ArticleMetadata(
        url=url,
        title=title,
        page_id=page_id,
        genre=genre,
        destination=destination,
        source_key=source_key,
        content_hash=content_hash,
        version_hint=version_hint,
        word_count=len(raw_text.split()),
        scraped_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )

    markdown = html_to_markdown(str(content_soup))

    log.info(f"  Title: {title}")
    log.info(f"  Genre: {genre} → {destination}")
    log.info(f"  source_key: {source_key}")
    log.info(f"  content_hash: {content_hash[:16]}...")
    log.info(f"  Words: {metadata.word_count}")

    return ScrapedArticle(metadata=metadata, markdown=markdown, raw_text=raw_text)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Scrape GeneXus wiki article")
    parser.add_argument("url", help="URL del artículo wiki")
    parser.add_argument(
        "--output-dir", default="data/scraped", help="Directorio de salida"
    )
    args = parser.parse_args()

    article = scrape(args.url)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_name = article.metadata.page_id or article.metadata.content_hash[:12]
    base = out_dir / safe_name

    md_path = base.with_suffix(".md")
    md_path.write_text(article.markdown, encoding="utf-8")
    log.info(f"Markdown → {md_path}")

    meta_path = base.with_suffix(".meta.json")
    meta_path.write_text(
        json.dumps(asdict(article.metadata), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info(f"Metadata → {meta_path}")

    print(f"\n{'='*60}")
    print(f"Artículo: {article.metadata.title}")
    print(f"Género: {article.metadata.genre} → {article.metadata.destination}")
    print(f"source_key: {article.metadata.source_key}")
    print(f"content_hash: {article.metadata.content_hash[:16]}...")
    print(f"Versión: {article.metadata.version_hint or 'N/A'}")
    print(f"Palabras: {article.metadata.word_count}")
    print(f"{'='*60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
