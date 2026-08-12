"""Bulk loader for pre-scraped documentation dumps.

Parses already-scraped sources (no HTTP) into RawDoc records and indexes them
into a retrieval collection in the Postgres store. Deliberately routes
everything to the retrieval layer (genre="guide") — these flat, HTML-stripped
dumps are too noisy to feed the binary-verdict syntax catalog, which must come
from structured sources under human curation.

Three input shapes are supported:
  - GeneXus base: `--- INÍCIO/FIM DO CONTEÚDO DE: {page_id}.html ---` blocks;
    the canonical wiki URL is reconstructed from the numeric page id.
  - WorkWithPlus: `--- URL: {url} ---` blocks separated by `====` rules, with a
    trailing `Page Id\\n{n}` footer that is stripped from the content.
  - Plain text files: one file = one document, for corpora that arrive already
    split (the Bantotal functional manual). Unlike the two above it takes paths,
    not a single blob of text, because there is no delimiter to split on.
"""

import logging
import re
import unicodedata
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

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


# --- Plain text files -------------------------------------------------------
#
# Word "Save as → Plain text" artifacts, measured across the 35 chapter files of
# the Bantotal functional manual: 0x07 marks table cells (683 occurrences) and
# 0x0c is a form feed (233). The form feeds are NOT page boundaries — measured,
# `Sector Contabilidad` has 7 of them but its own index goes up to "Pág. 10",
# because the figures account for the difference. So they become paragraph
# breaks, never page numbers.
_WORD_CELL_MARK = "\x07"
_FORM_FEED = "\x0c"

# Files that are tables of contents for the whole volume, not chapters. They are
# still useful as a completeness check (which chapters the index names vs. which
# ones the directory actually has), so they are reported rather than silently
# dropped — see split_index_files().
_INDEX_FILE_STEMS = frozenset({"indicetomo1", "indicetomo2"})

# Chapter files open with their own table of contents: a "CONTENIDO DEL CAPÍTULO
# DE ..." heading followed by "<section title> ... Pág. N" lines. It is pure
# lexical noise for retrieval — it repeats every section title, so it matches
# any query about the chapter — and its title→page map has nowhere to live while
# one file is one document. Dropped from the content.
_TOC_HEADING = re.compile(r"CONTENIDO\s+DEL\s+CAP[IÍ]TULO", re.IGNORECASE)
_TOC_PAGE_REF = re.compile(r"P[áa]g\.?\s*\d+\s*$")
_TOC_SCAN_LINES = 120  # bounded on purpose: never walk into real prose

_MULTI_BLANK = re.compile(r"\n{3,}")


def _slug(text: str) -> str:
    """Lowercase ASCII slug, for use inside a stable article id."""
    decomposed = unicodedata.normalize("NFD", text)
    ascii_only = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-") or "doc"


def _strip_toc_block(text: str) -> str:
    """Drop the leading per-chapter table of contents, if there is one.

    Conservative by construction: bails out unless one of the first non-blank
    lines is the TOC heading, and only scans a bounded prefix for the last
    "Pág. N" line. A chapter without a TOC comes back untouched.
    """
    lines = text.splitlines()
    head = [ln for ln in lines[:5] if ln.strip()]
    if not any(_TOC_HEADING.search(ln) for ln in head):
        return text

    last_page_ref = -1
    for i, line in enumerate(lines[:_TOC_SCAN_LINES]):
        if _TOC_PAGE_REF.search(line.strip()):
            last_page_ref = i
    if last_page_ref < 0:
        return text
    return "\n".join(lines[last_page_ref + 1 :]).lstrip("\n")


def _clean_word_text(text: str) -> str:
    """Normalize a Word plain-text export into paragraph-separated markdown-ish text.

    Assumes the caller read the file in TEXT mode so universal newlines already
    turned the bare CRs into \\n — see read_text_file() for why that matters.
    """
    text = text.replace(_WORD_CELL_MARK, "\t")
    text = text.replace(_FORM_FEED, "\n\n")
    # Strip the remaining control/private-use codepoints (Cc/Co) but keep the
    # whitespace that carries structure.
    text = "".join(
        c
        for c in text
        if c in "\n\t" or unicodedata.category(c) not in ("Cc", "Co")
    )
    text = _strip_toc_block(text)
    return _MULTI_BLANK.sub("\n\n", text).strip()


def read_text_file(path: Path) -> str:
    """Read a Word plain-text export and normalize it.

    TEXT mode with utf-8-sig, deliberately: these files are UTF-8 WITH BOM and
    use a bare CR as the paragraph terminator (measured: LF=1 in a 97 KB file —
    the only \\n is the final CRLF). utf-8-sig eats the BOM and universal
    newlines translate \\r -> \\n for free.

    Reading them as bytes instead yields ONE 27k-character line with no blank
    line anywhere, so the recursive splitter falls through to its last separator
    and cuts on arbitrary spaces: ~15% fewer chunks and worse boundaries, with
    no error raised. Decoding them as cp1252 raises UnicodeDecodeError outright.
    """
    with open(path, encoding="utf-8-sig") as fh:
        return _clean_word_text(fh.read())


def split_index_files(paths: Iterable[Path]) -> tuple[list[Path], list[Path]]:
    """Partition paths into (content, volume indexes).

    The volume indexes are not content — indexing them would return a list of
    chapter titles for any query. They are returned so the caller can report
    them, and cross-check the chapters they name against the files on disk.
    """
    content, indexes = [], []
    for p in sorted(paths):
        (indexes if _slug(p.stem) in _INDEX_FILE_STEMS else content).append(p)
    return content, indexes


def parse_text_files(
    paths: Iterable[Path], *, id_prefix: str, title_prefix: str = ""
) -> Iterator[RawDoc]:
    """Yield one RawDoc per plain text file.

    The title comes from the FILE NAME, not from the text: _first_meaningful_line
    is wrong here because the first line of several chapters is a TOC heading or
    blank.

    `url` is the absolute path of the source file. load_dump() passes it to both
    `source` and `url`, which is what makes a hit citable back to the exact file
    without adding any provenance column to the schema.
    """
    for path in sorted(paths):
        content = read_text_file(path)
        if not content:
            log.warning("empty after normalization, skipping: %s", path)
            continue
        chapter = path.stem.strip()
        yield RawDoc(
            source_key=f"{id_prefix.rstrip('/')}/{_slug(chapter)}",
            title=f"{title_prefix}{chapter}"[:120],
            content=content,
            url=str(path.resolve()),
            page_id=path.name,
        )


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
