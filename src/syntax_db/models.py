"""
Modelos SQLAlchemy para la Syntax DB (SQLite).
"""

from datetime import datetime
from sqlalchemy import (
    Column,
    String,
    Integer,
    Text,
    DateTime,
    JSON,
    Index,
    create_engine,
)
from sqlalchemy.orm import declarative_base, Session
from pathlib import Path

from src.paths import SYNTAX_DB_PATH

Base = declarative_base()


class GxSyntax(Base):
    __tablename__ = "gx_syntax"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, index=True)
    object_type = Column(String(100), nullable=False, index=True)
    category = Column(String(100))
    signature = Column(JSON)
    description = Column(Text)
    version_since = Column(String(50))
    source = Column(String(50))  # 'wiki' | 'xpz'
    source_doc_id = Column(String(255))
    kb_scope = Column(String(50))  # 'global' | 'kb_specific'
    created_at = Column(DateTime, default=datetime.utcnow)


class BtApi(Base):
    __tablename__ = "bt_apis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    endpoint = Column(String(255), nullable=False)
    method = Column(String(10))
    path = Column(String(500))
    params = Column(JSON)
    request_body = Column(JSON)
    responses = Column(JSON)
    auth_type = Column(String(50))
    source_doc_id = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)


class ValidationLog(Base):
    __tablename__ = "validation_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    tool = Column(String(100))
    input_snippet = Column(Text)
    verdict = Column(String(20))  # VÁLIDO | INVÁLIDO | NO_VERIFICABLE
    matched_entry_id = Column(Integer)
    version_queried = Column(String(50))
    source = Column(String(50))
    notes = Column(Text)


class MissingMethod(Base):
    __tablename__ = "missing_methods"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(String(255), nullable=False, unique=True)  # nombre normalizado
    object_type = Column(String(100))
    first_seen = Column(DateTime, default=datetime.utcnow)
    hit_count = Column(Integer, default=1)
    status = Column(String(50), default="pending")  # pending | curated | rejected


class SyntaxCandidate(Base):
    """Staging area for auto-discovered catalog entries awaiting human curation.

    Extraction from scraped text is heuristic and noisy, so candidates never
    land directly in the authoritative GxSyntax catalog — a human promotes them
    (see candidate_repository.promote_candidate), keeping the binary-verdict
    layer trustworthy.
    """

    __tablename__ = "syntax_candidates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, index=True)
    object_type = Column(String(100))
    category = Column(String(100))
    signature = Column(JSON)
    description = Column(Text)
    evidence = Column(Text)  # source snippet backing the candidate
    source_doc_id = Column(String(255))  # provenance URL
    hit_count = Column(Integer, default=1)
    status = Column(String(50), default="pending")  # pending | promoted | rejected
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index("ix_candidate_name_objtype", "name", "object_type"),)


# ─── Engine / session helpers ─────────────────────────────────────────────────

_engine = None


def get_engine(db_path: str | Path | None = None):
    global _engine
    if _engine is None:
        path = Path(db_path) if db_path is not None else SYNTAX_DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{path}", echo=False)
        Base.metadata.create_all(_engine)
    return _engine


def get_session(db_path: str | Path | None = None):
    engine = get_engine(db_path)
    return Session(engine)
