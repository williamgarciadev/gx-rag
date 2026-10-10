"""Loader for local documentation files (PDF, Markdown) → RawDoc.

Complements `bulk_loader` (which parses pre-scraped web dumps): this one reads
files off disk, so the Bantotal manuals — which exist only as PDFs, never as
wiki pages — can reach the retrieval layer.

PDFs are emitted **one RawDoc per chapter**, carrying the PDF page range the
chapter was drawn from. Extraction lives in `bt_pdf_extract`, which rebuilds
each page from font and geometry: headings by font (FranklinGothic-Demi, since
body and headings share 8pt in some manuals), parametrisation tables by
region/anchor into Markdown pipes, running heads/footers by pattern, TOC pages
dropped whole. This module only maps its `Chapter`s onto the RawDoc contract.

The unit used to be the *page*, reflowed by joining overlapping blocks with
" | ". A page is the tightest citation ("MDI-10201 p.412"), but it severs every
table and paragraph that crosses a page break and never recovers the heading
structure — a chunk could land mid-table with no header row in sight. A chapter
keeps both intact, and the citation survives as a range ("pp.408-415"), which is
still checkable against the PDF.
"""

import logging
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

from src.ingestion.bt_pdf_extract import DOC_CODE_PREFIXES, Chapter, extract_chapters
from src.ingestion.bulk_loader import RawDoc

log = logging.getLogger(__name__)


# "MDI-10201-GL-V3R1.20 - Sistema de Préstamos" → code, lang, version, title.
# Matches all 7 real manuals; the off-convention files (a slide deck, derived
# .md files) fall through and simply carry no manual metadata.
#: Prefixes come from `bt_pdf_extract`, which needs the same list to recognise a
#: footer line. Keeping a private copy here is what let PRD-99000 ingest with
#: citation metadata while its footers leaked in as content.
#:
#: Leaving a prefix out is silent — the file still ingests, but with no manual
#: code or version, so its chunks cannot be cited or filtered. ECP numbers its
#: documents with six digits (ECP-000302) where the rest use five, and skips the
#: localization segment altogether — hence `loc` being optional. That segment is
#: a LOCALIZATION (GL = Global; CO, UY, PE = country), not a language.
_MANUAL_NAME = re.compile(
    rf"^(?P<code>(?:{'|'.join(DOC_CODE_PREFIXES)})-\d{{4,6}})"
    r"(?:-(?P<loc>[A-Z]{2}))?-"
    r"(?P<version>V\d+R\d+(?:\.\d+)?)\s*-\s*(?P<title>.+)$"
)

# A piece under this many words is a stub on its own — a heading plus one line —
# and indexed alone it is a keyword-dense chunk with nothing to answer from,
# competing against the piece that does have the answer. It is FOLDED INTO the
# preceding piece rather than dropped: the split leaves 85 such pieces in
# MDI-10201 (3% of its text), and in a parametrisation manual twenty words is
# often a whole fact ("Ordinal 46: Comisión por Mora — indica si aplica").
# The floor stays low for the same reason: a four-row table is barely 30 words.
_MIN_CHAPTER_WORDS = 25

# Past this many pages a "chapter" is almost certainly two, whose boundary
# heading the font heuristic missed. Not dropped — the text is real and the span
# still cites truthfully, only coarsely — but the curator should see it in the
# dry-run output and decide.
_WIDE_CHAPTER_PAGES = 25


def _manual_meta(stem: str) -> dict:
    m = _MANUAL_NAME.match(stem)
    if m is None:
        log.warning("Filename off the manual convention, no manual metadata: %s", stem)
        return {"manual": "", "manual_version": "", "manual_title": stem}
    meta = {
        "manual": m.group("code"),
        "manual_version": m.group("version"),
        "manual_title": m.group("title").strip(),
    }
    if m.group("loc"):
        meta["localization"] = m.group("loc")
    return meta


def _page_ref(start: int, end: int) -> tuple[str, str]:
    """(range, label): ('41', 'p.41') for one page, ('41-48', 'pp.41-48').

    The range is the machine-readable form (metadata, `page_id`); the label is
    what a reader sees inside the chunk. Both come from one branch so they can
    never disagree about whether the chapter is a single page.
    """
    if start == end:
        return str(start), f"p.{start}"
    return f"{start}-{end}", f"pp.{start}-{end}"


#: Global is the default and says nothing; only a country localization is worth
#: the reader's attention. Showing GL too would also change the text of every
#: already-indexed chunk, and `content_hash` (content only) would not notice.
_DEFAULT_LOCALIZATION = "GL"


def _localization_label(manual: dict) -> str:
    """'localización UY' for a country-specific document, '' otherwise."""
    loc = manual.get("localization", "")
    return f"localización {loc}" if loc and loc != _DEFAULT_LOCALIZATION else ""


def _breadcrumb(manual: dict, chapter_title: str, section: str, page_label: str) -> str:
    """Situating header prepended to each chunk's text.

    What a consuming agent reads is the chunk body, so the manual, chapter,
    section and page belong *in* it — not only in metadata, which only serves
    filtering. `section` is empty unless a wide chapter was split, in which case
    both levels are shown: "MDI-10201 … · 2.0 Esquemas · 2.3 Devengamiento · p.140".
    """
    label = " ".join(x for x in (manual["manual"], manual["manual_title"]) if x)
    parts = [p for p in (label, _localization_label(manual), chapter_title, section, page_label) if p]
    return f"[{' · '.join(parts)}]"


def _coalesce_stubs(chapters: list[Chapter]) -> list[Chapter]:
    """Fold stub pieces into the piece before them, so nothing is dropped.

    Only folds within one chapter: a stub that opens a *different* chapter is
    left alone rather than glued onto the previous chapter's tail, which would
    make its citation lie. Widening the previous piece's page span is the whole
    cost, and it stays truthful.
    """
    out: list[Chapter] = []
    for chapter in chapters:
        long_enough = len(chapter.markdown.split()) >= _MIN_CHAPTER_WORDS
        if long_enough or not out or out[-1].title != chapter.title:
            out.append(chapter)
            continue
        previous = out[-1]
        # The stub repeats the chapter H1; drop it before folding the rest in.
        body = "\n\n".join(
            block
            for block in chapter.markdown.split("\n\n")
            if not block.startswith("# ")
        ).strip()
        out[-1] = previous._replace(
            markdown=f"{previous.markdown}\n\n{body}" if body else previous.markdown,
            page_end=max(previous.page_end, chapter.page_end),
        )
    return out


def _pdf_docs(path: Path) -> Iterator[RawDoc]:
    stem = path.stem
    uri = path.as_uri()
    manual = _manual_meta(stem)

    for number, chapter in enumerate(_coalesce_stubs(extract_chapters(path)), start=1):
        pages = chapter.page_end - chapter.page_start + 1
        if pages > _WIDE_CHAPTER_PAGES:
            log.warning(
                "Chapter spans %d pages — a boundary heading was likely missed: %s ch%02d %s",
                pages, stem, number, chapter.title,
            )

        page_range, page_label = _page_ref(chapter.page_start, chapter.page_end)
        # The most specific heading is what a citation should name: the split
        # sub-heading when there is one, the chapter title otherwise.
        section = chapter.section or chapter.title
        yield RawDoc(
            source_key=f"{uri}#ch={number}",
            title=f"{stem} — {section}" if section else f"{stem} — cap.{number}",
            content=chapter.markdown,
            url=uri,
            page_id=page_range,
            meta={
                **manual,
                # `page` is the citation field the retriever surfaces; keep the
                # numeric bounds beside it so a filter does not parse the range.
                "page": page_range,
                "page_start": chapter.page_start,
                "page_end": chapter.page_end,
                "chapter": number,
                "section": section,
                "doc_type": "manual_pdf_chapter",
            },
            chunk_prefix=_breadcrumb(manual, chapter.title, chapter.section, page_label),
        )


def parse_frontmatter(content: str) -> dict:
    """Extrae el frontmatter YAML si el archivo comienza con '---'."""
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    raw_yaml = parts[1].strip()
    if not raw_yaml:
        return {}
    try:
        import yaml

        data = yaml.safe_load(raw_yaml)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _markdown_title(content: str, stem: str) -> str:
    frontmatter = parse_frontmatter(content)
    if frontmatter and frontmatter.get("title"):
        return str(frontmatter["title"]).strip()

    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]

    for line in body.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line:
            break
    return stem


def _text_docs(path: Path) -> Iterator[RawDoc]:
    content = path.read_text(encoding="utf-8", errors="replace").strip()
    if not content:
        return
    uri = path.as_uri()
    title = _markdown_title(content, path.stem)
    meta = {"doc_type": "markdown", "manual_title": title}
    frontmatter = parse_frontmatter(content)
    if frontmatter:
        meta.update(frontmatter)
    yield RawDoc(
        source_key=uri,
        title=title,
        content=content,
        url=uri,
        page_id="",
        meta=meta,
        chunk_prefix=f"[{title}]",
    )


_READERS = {".pdf": _pdf_docs, ".md": _text_docs, ".txt": _text_docs}


def iter_supported_files(root: Path) -> Iterator[Path]:
    """Walk `root` recursively, yielding only files this loader can read."""
    for path in sorted(Path(root).rglob("*")):
        if path.is_file() and path.suffix.lower() in _READERS:
            yield path


def parse_local_docs(paths: Iterable[Path]) -> Iterator[RawDoc]:
    """Yield RawDoc records for each local file, dispatched by extension.

    Unsupported extensions are skipped with a warning rather than raising —
    a directory sweep should not abort because one stray file is unreadable.
    """
    for raw in paths:
        path = Path(raw)
        reader = _READERS.get(path.suffix.lower())
        if reader is None:
            log.warning("Skipping unsupported file type: %s", path)
            continue
        yield from reader(path)
