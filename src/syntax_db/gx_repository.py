"""
Repositorio GeneXus — operaciones contra la Syntax DB.
"""

import re
import logging
from typing import Literal

from src.syntax_db.models import (
    BtApi,
    GxSyntax,
    MissingMethod,
    ValidationLog,
    get_session,
)

log = logging.getLogger(__name__)


Verdict = Literal["VÁLIDO", "INVÁLIDO", "NO_VERIFICABLE"]


def _normalize_token(snippet: str) -> str:
    """Normaliza un snippet a tokens clave para búsqueda."""
    # Extraer palabras alfanuméricas + guiones bajos
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", snippet)
    # Filtrar palabras comunes de programación
    stop = {
        "if",
        "else",
        "for",
        "while",
        "do",
        "return",
        "var",
        "let",
        "const",
        "function",
        "class",
        "import",
        "export",
        "new",
        "this",
        "self",
        "true",
        "false",
        "null",
        "none",
        "async",
        "await",
        "def",
        "in",
        "or",
        "and",
    }
    return " ".join(t for t in tokens if t.lower() not in stop).strip()


def _log_verdict(
    tool: str,
    snippet: str,
    verdict: Verdict,
    matched_id: int | None,
    version: str | None,
    source: str | None,
) -> None:
    """Registra cada veredicto en validation_log."""
    try:
        session = get_session()
        entry = ValidationLog(
            tool=tool,
            input_snippet=snippet[:500],
            verdict=verdict,
            matched_entry_id=matched_id,
            version_queried=version,
            source=source,
        )
        session.add(entry)
        session.commit()
        session.close()
    except Exception as exc:
        log.error(f"Failed to log verdict: {exc}")


def _record_missing(token: str, object_type: str | None, version: str | None) -> None:
    """Registra un método/token no verificado para curación posterior."""
    try:
        session = get_session()
        existing = session.query(MissingMethod).filter_by(token=token).first()
        if existing:
            existing.hit_count += 1
            existing.status = "pending"
        else:
            session.add(
                MissingMethod(
                    token=token,
                    object_type=object_type,
                    status="pending",
                )
            )
        session.commit()
        session.close()
    except Exception as exc:
        log.error(f"Failed to record missing method: {exc}")


def _verify_bt_api(snippet: str, token: str, version: str | None) -> dict:
    """Verify a token against the BtApi catalog (endpoint names)."""
    session = get_session()
    try:
        entry = session.query(BtApi).filter(BtApi.endpoint == token).first()
        if entry:
            _log_verdict(
                "verify_syntax", snippet, "VÁLIDO", entry.id, version, "bt_api"
            )
            return {
                "verdict": "VÁLIDO",
                "name": entry.endpoint,
                "object_type": "BtApi",
                "signature": {"method": entry.method, "path": entry.path},
                "source": "bt_api",
                "notes": f"Endpoint Bantotal válido ({entry.method} {entry.path})",
            }
        _log_verdict("verify_syntax", snippet, "NO_VERIFICABLE", None, version, None)
        _record_missing(token, "BtApi", version)
        return {
            "verdict": "NO_VERIFICABLE",
            "reason": "Endpoint no encontrado en el catálogo bt_apis. Verificar manualmente.",
            "token": token,
        }
    finally:
        session.close()


def verify_syntax(
    snippet: str,
    language: str = "gx",
    version: str | None = None,
) -> dict:
    """
    Verifica un snippet contra el catálogo gx_syntax.
    Retorna veredicto con procedencia.
    """
    token = _normalize_token(snippet)

    # bt_api snippets are verified against the BtApi catalog, NOT GxSyntax.
    if language == "bt_api":
        return _verify_bt_api(snippet, token, version)

    session = get_session()

    # Búsqueda exacta en nombre
    entry = session.query(GxSyntax).filter(GxSyntax.name == token).first()

    if entry:
        # NOTE: version filtering is not enforced yet — version_since is not a
        # normalized/comparable format, so we surface the catalog version and
        # flag a mismatch in notes, but do not gate the verdict on it.
        version_note = ""
        if version and entry.version_since and version != entry.version_since:
            version_note = (
                f" (versión pedida {version}; catálogo indica {entry.version_since})"
            )
        session.close()
        _log_verdict(
            "verify_syntax", snippet, "VÁLIDO", entry.id, version, entry.source
        )
        return {
            "verdict": "VÁLIDO",
            "name": entry.name,
            "object_type": entry.object_type,
            "signature": entry.signature,
            "version": entry.version_since,
            "source": entry.source,
            "notes": f"Válido según {entry.source} ({entry.object_type}){version_note}",
        }

    # Buscar por similaridad en nombre
    similar = (
        session.query(GxSyntax)
        .filter(GxSyntax.name.like(f"%{token[:8]}%"))
        .limit(5)
        .all()
    )

    session.close()

    if not similar:
        # No encontrado → NO_VERIFICABLE
        _log_verdict("verify_syntax", snippet, "NO_VERIFICABLE", None, version, None)
        _record_missing(token, None, version)
        return {
            "verdict": "NO_VERIFICABLE",
            "reason": "No encontrado en el catálogo. Verificar manualmente.",
            "token": token,
        }

    # Encontrado similar → INVÁLIDO
    _log_verdict("verify_syntax", snippet, "INVÁLIDO", None, version, None)
    names = [s.name for s in similar]
    return {
        "verdict": "INVÁLIDO",
        "reason": f"'{token}' no coincide exactamente con ninguna entrada del catálogo.",
        "sugerencias": names[:5],
    }


def list_methods(object_type: str, version: str | None = None) -> list[str]:
    """Lista todos los métodos de un tipo de objeto."""
    session = get_session()
    query = session.query(GxSyntax.name).filter(GxSyntax.object_type == object_type)
    if version:
        query = query.filter(GxSyntax.version_since == version)
    results = [r[0] for r in query.distinct().all()]
    session.close()
    return results


def get_signature(
    name: str, object_type: str, version: str | None = None
) -> dict | None:
    """Obtiene la firma exacta de un método/objeto."""
    session = get_session()
    query = session.query(GxSyntax).filter(
        GxSyntax.name == name,
        GxSyntax.object_type == object_type,
    )
    if version:
        query = query.filter(GxSyntax.version_since == version)
    entry = query.first()
    session.close()
    if not entry:
        return None
    return {
        "name": entry.name,
        "object_type": entry.object_type,
        "signature": entry.signature,
        "description": entry.description,
        "version": entry.version_since,
        "source": entry.source,
    }
