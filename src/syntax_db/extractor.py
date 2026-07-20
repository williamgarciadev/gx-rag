"""Heuristic extractor of catalog candidates from scraped documentation.

Discovery only — output goes to the SyntaxCandidate staging table for human
review, never straight into the authoritative catalog. The heuristics are
deliberately conservative: a block is only a candidate when it has a short,
identifier-like title AND an explicit "Syntax" section, so guides and prose are
skipped rather than polluting the queue.
"""

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass

log = logging.getLogger(__name__)

_MAX_TITLE_WORDS = 5
_SIGNATURE_MAX = 200
# First keyword wins → object_type. Ordered from most to least specific.
_OBJECT_TYPES = [
    ("command", "Command"),
    ("property", "Property"),
    ("method", "Method"),
    ("function", "Function"),
    ("event", "Event"),
    ("clause", "Clause"),
    ("rule", "Rule"),
    ("attribute", "Attribute"),
    ("object", "Object"),
]
_SIGNATURE_STOP = re.compile(r"\b(Where|See also|Description|Scope|Example)\b")


@dataclass(frozen=True)
class CandidateDraft:
    name: str
    object_type: str
    category: str | None
    signature: str | None
    description: str | None
    evidence: str | None
    source_doc_id: str | None


def _detect_object_type(content: str) -> str:
    low = content.lower()
    for kw, label in _OBJECT_TYPES:
        if kw in low:
            return label
    return "Unknown"


def _extract_signature(content: str) -> str | None:
    m = re.search(r"(?i)\bsyntax\b\s*[:\-]?\s*(.+)", content)
    if not m:
        return None
    tail = m.group(1).strip()
    stop = _SIGNATURE_STOP.search(tail)
    if stop:
        tail = tail[: stop.start()].strip()
    return tail[:_SIGNATURE_MAX] or None


def _is_candidate(title: str, content: str) -> bool:
    if not title or len(title.split()) > _MAX_TITLE_WORDS:
        return False
    return bool(re.search(r"(?i)\bsyntax\b", content))


def extract_candidates(docs: Iterable) -> list[CandidateDraft]:
    """Extract CandidateDrafts from docs (duck-typed: .title, .content, .url)."""
    drafts: list[CandidateDraft] = []
    for doc in docs:
        title = (getattr(doc, "title", "") or "").strip()
        content = getattr(doc, "content", "") or ""
        if not _is_candidate(title, content):
            continue
        signature = _extract_signature(content)
        if not signature:
            continue
        drafts.append(
            CandidateDraft(
                name=title,
                object_type=_detect_object_type(content),
                category=None,
                signature=signature,
                description=content[:200],
                evidence=content[:300],
                source_doc_id=getattr(doc, "url", None),
            )
        )
    return drafts


def store_drafts(drafts: Iterable[CandidateDraft]) -> int:
    """Persist drafts into the staging table. Returns the count stored."""
    from src.syntax_db.candidate_repository import store_candidate

    n = 0
    for d in drafts:
        store_candidate(
            name=d.name,
            object_type=d.object_type,
            category=d.category,
            signature={"raw": d.signature} if d.signature else None,
            description=d.description,
            evidence=d.evidence,
            source_doc_id=d.source_doc_id,
        )
        n += 1
    return n
