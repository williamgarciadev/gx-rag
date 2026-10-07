"""
Modelos SQLAlchemy para la Syntax DB (SQLite).
"""

import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import Session, declarative_base

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


DEFAULT_SQLITE_PATH = "data/syntax.db"


def get_engine(db_path: str | Path | None = None):
    """Devuelve el engine. Un db_path explicito gana; si no, manda GXRAG_DSN.

    Con GXRAG_DSN definido y sin db_path, el catalogo de sintaxis vive en la
    misma base que los chunks: un solo motor, un solo backup, y las consultas
    pueden cruzar catalogo y corpus. Sin la variable cae a SQLite.

    OJO con la precedencia: antes el DSN ganaba SIEMPRE y el db_path recibido se
    descartaba en silencio. Los tests pasaban un SQLite temporal creyendo que
    aislaban y terminaban escribiendo en la base real (se detecto cuando un
    assert de 1 candidato encontro 816). Un argumento explicito es mas
    especifico que una variable de entorno: ahora gana el argumento.
    """
    global _engine
    if _engine is None:
        dsn = None if db_path is not None else os.environ.get("GXRAG_DSN")
        if dsn:
            # SQLAlchemy asume psycopg2 con el esquema pelado; en este proyecto
            # el driver instalado es psycopg3.
            if dsn.startswith("postgresql://"):
                dsn = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
            _engine = create_engine(dsn, echo=False)
        else:
            path = Path(db_path if db_path is not None else DEFAULT_SQLITE_PATH)
            path.parent.mkdir(parents=True, exist_ok=True)
            _engine = create_engine(f"sqlite:///{path}", echo=False)
        Base.metadata.create_all(_engine)
    return _engine


def get_session(db_path: str | Path | None = None):
    engine = get_engine(db_path)
    return Session(engine)
