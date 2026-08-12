"""Repository for syntax candidates — the human-gated bridge into the catalog.

store_candidate / list_candidates / reject_candidate manage the staging area;
promote_candidate is the ONLY path that writes an authoritative GxSyntax row,
and it is meant to be driven by a human (via scripts/candidates.py), never
automatically. That gate is what keeps verify_syntax's verdicts trustworthy.
"""

import logging
from typing import Any

from src.syntax_db.models import GxSyntax, SyntaxCandidate, get_session

log = logging.getLogger(__name__)


def store_candidate(
    name: str,
    object_type: str,
    *,
    category: str | None = None,
    signature: Any | None = None,
    description: str | None = None,
    evidence: str | None = None,
    source_doc_id: str | None = None,
) -> int:
    """Upsert a candidate keyed by (name, object_type); bump hit_count on repeats."""
    session = get_session()
    try:
        existing = (
            session.query(SyntaxCandidate)
            .filter_by(name=name, object_type=object_type)
            .first()
        )
        if existing:
            existing.hit_count += 1
            session.commit()
            return existing.id
        candidate = SyntaxCandidate(
            name=name,
            object_type=object_type,
            category=category,
            signature=signature,
            description=description,
            evidence=evidence,
            source_doc_id=source_doc_id,
        )
        session.add(candidate)
        session.commit()
        return candidate.id
    finally:
        session.close()


def list_candidates(status: str = "pending") -> list[dict]:
    """Return candidates with the given status (most-seen first)."""
    session = get_session()
    try:
        rows = (
            session.query(SyntaxCandidate)
            .filter(SyntaxCandidate.status == status)
            .order_by(SyntaxCandidate.hit_count.desc())
            .all()
        )
        return [
            {
                "id": r.id,
                "name": r.name,
                "object_type": r.object_type,
                "signature": r.signature,
                "hit_count": r.hit_count,
                "status": r.status,
                "source_doc_id": r.source_doc_id,
            }
            for r in rows
        ]
    finally:
        session.close()


def promote_candidate(candidate_id: int) -> dict | None:
    """Promote a candidate into the authoritative GxSyntax catalog (human action)."""
    session = get_session()
    try:
        c = session.query(SyntaxCandidate).filter_by(id=candidate_id).first()
        if not c or c.status == "promoted":
            return None
        entry = GxSyntax(
            name=c.name,
            object_type=c.object_type,
            category=c.category,
            signature=c.signature,
            description=c.description,
            version_since=None,
            source="wiki",
            source_doc_id=c.source_doc_id,
            kb_scope="global",
        )
        session.add(entry)
        c.status = "promoted"
        session.commit()
        return {"id": entry.id, "name": entry.name, "object_type": entry.object_type}
    finally:
        session.close()


def reject_candidate(candidate_id: int) -> bool:
    """Mark a candidate as rejected so it drops out of the pending queue."""
    session = get_session()
    try:
        c = session.query(SyntaxCandidate).filter_by(id=candidate_id).first()
        if not c:
            return False
        c.status = "rejected"
        session.commit()
        return True
    finally:
        session.close()
