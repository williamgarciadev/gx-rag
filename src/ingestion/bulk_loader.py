"""Bulk loader for pre-scraped documentation dumps.

Parses delimiter-separated text dumps (already scraped, no HTTP) into RawDoc
records and indexes them into a ChromaDB retrieval collection. Deliberately
routes everything to the retrieval layer (genre="guide") — these flat,
HTML-stripped dumps are too noisy to feed the binary-verdict syntax catalog,
which must come from structured sources under human curation.

Two dump formats are supported:
  - GeneXus base: `--- INÍCIO/FIM DO CONTEÚDO DE: {page_id}.html ---` blocks;
    the canonical wiki URL is reconstructed from the numeric page id.
  - WorkWithPlus: `--- URL: {url} ---` blocks separated by `====` rules, with a
    trailing `Page Id\\n{n}` footer that is stripped from the content.
"""

import logging
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

log = logging.getLogger(__name__)

GENEXUS_WIKI_URL = "https://wiki.genexus.com/commwiki/servlet/wiki?{page_id}"

_GENEXUS_BLOCK = re.compile(
    r"--- INÍCIO DO CONTEÚDO DE: (?P<id>.+?) ---\n"
    r"(?P<body>.*?)"
    r"\n--- FIM DO CONTEÚDO DE: (?P=id) ---",
    re.DOTALL,
)
_WWP_SPLIT = re.compile(r"--- URL: (?P<url>.+?) ---\n")
_WWP_SEPARATOR = re.compile(r"^=+\s*$", re.MULTILINE)
_WWP_PAGE_ID_FOOTER = re.compile(r"\n?Page Id\s*\n\s*(?P<pid>\d+)\s*$")
_NEWEST_VERSION = "Newest Version"


@dataclass(frozen=True)
class RawDoc:
    """A single document extracted from a dump, ready to index."""

    source_key: str  # canonical URL — used as the ChromaDB article id
    title: str
    content: str
    url: str
    page_id: str


@dataclass
class LoadReport:
    """Summary of a load_dump run."""

    total: int = 0
    indexed: int = 0
    skipped: int = 0
    reindexed: int = 0

    def record(self, status: str) -> None:
        self.total += 1
        if status == "indexed":
            self.indexed += 1
        elif status == "skipped":
            self.skipped += 1
        elif status == "reindexed":
            self.reindexed += 1


def _first_meaningful_line(body: str) -> str:
    for line in body.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _derive_title(body: str) -> str:
    """Best-effort title from flat text.

    GeneXus dumps prefix the page title then repeat it after 'Newest Version'
    (e.g. 'Default rule Newest Version Assigns...'); take the text before that
    marker. Otherwise fall back to the first line, capped to a sane length.
    """
    first = _first_meaningful_line(body)
    if _NEWEST_VERSION in first:
        first = first.split(_NEWEST_VERSION, 1)[0].strip()
    return first[:120]


def parse_genexus_dump(text: str) -> Iterator[RawDoc]:
    """Yield RawDoc per `INÍCIO/FIM` block, reconstructing the canonical wiki URL."""
    for m in _GENEXUS_BLOCK.finditer(text):
        raw_id = m.group("id").strip()
        page_id = raw_id.removesuffix(".html")
        body = m.group("body").strip()
        if not body:
            continue
        url = GENEXUS_WIKI_URL.format(page_id=page_id)
        yield RawDoc(
            source_key=url,
            title=_derive_title(body),
            content=body,
            url=url,
            page_id=page_id,
        )


def parse_wwp_dump(text: str) -> Iterator[RawDoc]:
    """Yield RawDoc per `--- URL: ... ---` block, stripping the Page Id footer."""
    parts = _WWP_SPLIT.split(text)
    # parts[0] is the preamble; then alternating (url, body, url, body, ...).
    it = iter(parts[1:])
    for url in it:
        body = next(it, "")
        url = url.strip()
        # Drop trailing separator rules and the "Page Id / <n>" footer.
        body = _WWP_SEPARATOR.sub("", body).strip()
        footer = _WWP_PAGE_ID_FOOTER.search(body)
        page_id = footer.group("pid") if footer else _page_id_from_url(url)
        if footer:
            body = body[: footer.start()].strip()
        if not body:
            continue
        yield RawDoc(
            source_key=url,
            title=_first_meaningful_line(body)[:120],
            content=body,
            url=url,
            page_id=page_id,
        )


def _page_id_from_url(url: str) -> str:
    m = re.search(r"wiki\?(\d+)", url)
    return m.group(1) if m else ""


def load_dump(
    docs: Iterable[RawDoc],
    collection: str = "gx_docs",
    *,
    index_fn: Callable[..., str] | None = None,
    hash_fn: Callable[[str], str] | None = None,
    on_progress: Callable[[int, RawDoc, str], None] | None = None,
) -> LoadReport:
    """Index each RawDoc idempotently into the retrieval collection.

    Dumps always land as genre="guide" (retrieval layer) — never the syntax
    catalog. `index_fn`/`hash_fn` are injectable for testing.
    """
    if index_fn is None:
        from src.vectorstore.pg_indexer import reindex_if_changed

        index_fn = reindex_if_changed
    if hash_fn is None:
        from scripts.scrape_wiki import compute_hash

        hash_fn = compute_hash

    report = LoadReport()
    for i, doc in enumerate(docs):
        content_hash = hash_fn(doc.content)
        status = index_fn(
            doc.source_key,
            doc.title,
            doc.content,
            doc.url,
            "guide",
            doc.url,
            content_hash,
            collection,
        )
        report.record(status)
        if on_progress is not None:
            on_progress(i, doc, status)
    return report
