"""
Repositorio GeneXus — operaciones contra la Syntax DB.
"""

from __future__ import annotations

import difflib
import logging
import re
from typing import Literal

from sqlalchemy import func

from src.syntax_db.models import (
    BtApi,
    GxSyntax,
    MissingMethod,
    ValidationLog,
    get_session,
)

log = logging.getLogger(__name__)

Verdict = Literal["VÁLIDO", "INVÁLIDO", "NO_VERIFICABLE", "PISTA"]

CATALOG_SOURCE_VERSION = 18

_INVOCABILITY = {
    "command": 0,
    "function": 1,
    "method": 2,
    "data type": 3,
    "event": 4,
    "property": 5,
}

MIN_SIMILARITY_TOKEN = 3

_ENTRY_SUFFIXES = (
    "method",
    "property",
    "function",
    "rule",
    "event",
    "command",
    "clause",
    "object",
    "attribute",
)

# &fecha.AddYears(5) -> AddYears   |   &col.Add(&item) -> Add
_MEMBER_CALL = re.compile(r"\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(")
# &var.ResourceName (acceso a propiedad o método sin paréntesis)
_MEMBER_ACCESS = re.compile(r"\.\s*([A-Za-z_][A-Za-z0-9_]*)")

# Patrón receptor.miembro: &fecha.AddYears(...) o Date.AddDays
_RECEIVER_MEMBER = re.compile(r"([&]?[A-Za-z0-9_]+)?\.\s*([A-Za-z_][A-Za-z0-9_]*)")


def _extract_candidate(snippet: str) -> tuple[str | None, str]:
    """Extrae el receptor (clase/variable) y el identificador de método/función de un snippet.

    Ejemplos:
    - '&fecha.ToFormattedString(3)' -> ('&fecha', 'ToFormattedString')
    - 'Date.AddDays(1)'             -> ('Date', 'AddDays')
    - 'TrimLeftInexistente'         -> (None, 'TrimLeftInexistente')
    - 'Null(&var)'                  -> (None, 'Null')
    """
    s = snippet.strip()

    # 1. Invocación con punto (&var.Metodo(...) o Tipo.Metodo(...) o .Metodo(...))
    m = _RECEIVER_MEMBER.search(s)
    if m:
        receiver, member = m.groups()
        return receiver, member

    # 2. Llamada a función suelta: Func(...)
    func_match = re.search(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(", s)
    if func_match:
        return None, func_match.group(1)

    # 3. Token normalizado completo si no hay punto ni llamada
    return None, _normalize_token(s)


def _member_tokens(snippet: str) -> list[str]:
    """Miembros invocados o accedidos en un snippet, del más probable al menos."""
    vistos: list[str] = []
    for rx in (_MEMBER_CALL, _MEMBER_ACCESS):
        for m in rx.findall(snippet):
            if m not in vistos:
                vistos.append(m)
    return vistos


def _lookup_entry(session, nombres: list[str], receiver_type: str | None = None) -> list[GxSyntax]:
    """Entradas de gx_syntax que matchean alguno de `nombres`, mejor primero."""
    for base in nombres:
        # Si tenemos un tipo receptor conocido (ej. Date), filtrar por él primero
        if receiver_type and not receiver_type.startswith("&"):
            exacta_tipo = (
                session.query(GxSyntax)
                .filter(
                    func.lower(GxSyntax.name) == base.lower(),
                    func.lower(GxSyntax.object_type) == receiver_type.lower(),
                )
                .all()
            )
            if exacta_tipo:
                return exacta_tipo

        for nombre in [base] + [f"{base} {s}" for s in _ENTRY_SUFFIXES]:
            n = nombre.lower()
            exacta = (
                session.query(GxSyntax)
                .filter(func.lower(GxSyntax.name) == n)
                .all()
            )
            if exacta:
                return sorted(
                    exacta,
                    key=lambda e: _INVOCABILITY.get((e.object_type or "").lower(), 99),
                )

            calificadas = (
                session.query(GxSyntax)
                .filter(
                    func.lower(GxSyntax.name).like(f"{n} %")
                    | func.lower(GxSyntax.name).like(f"% {n}")
                    | func.lower(GxSyntax.name).like(f"% {n} %")
                )
                .order_by(GxSyntax.name)
                .all()
            )
            if calificadas:
                return sorted(
                    calificadas,
                    key=lambda e: _INVOCABILITY.get((e.object_type or "").lower(), 99),
                )
    return []


def _normalize_token(snippet: str) -> str:
    """Normaliza un snippet a tokens clave para búsqueda."""
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", snippet)
    stop = {
        "if", "else", "for", "while", "do", "return", "var", "let",
        "const", "function", "class", "import", "export", "new", "this",
        "self", "true", "false", "null", "none", "async", "await", "def",
        "in", "or", "and", "then", "endif", "endfor", "event", "endevent",
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


def _find_similar(session, token: str) -> list[str]:
    """Busca identificadores similares en el catálogo para evitar alucinaciones."""
    all_names = [r[0] for r in session.query(GxSyntax.name).distinct().all()]
    t_lower = token.lower()
    matches: list[str] = []

    # 1. Coincidencia por subcadena / prefijo mutuo
    for n in all_names:
        n_lower = n.lower()
        if len(n) >= 3 and (
            n_lower in t_lower
            or t_lower.startswith(n_lower)
            or (len(t_lower) >= 4 and n_lower.startswith(t_lower[:4]))
        ):
            if n not in matches:
                matches.append(n)

    # 2. Distancia Levenshtein / difflib si no hubo coincidencias directas
    if not matches:
        matches = difflib.get_close_matches(token, all_names, n=5, cutoff=0.4)

    return matches


def _version_rank(version: str | None) -> int | None:
    if not version:
        return None
    m = re.search(r"\d+", version)
    return int(m.group()) if m else None


def verify_syntax(
    snippet: str,
    language: str = "gx",
    version: str | None = None,
) -> dict:
    """
    Verifica un snippet contra el catálogo gx_syntax.
    Retorna veredicto con procedencia.
    """
    receiver, candidate_token = _extract_candidate(snippet)
    token = _normalize_token(snippet)

    if not candidate_token and not token:
        _log_verdict("verify_syntax", snippet, "NO_VERIFICABLE", None, version, None)
        return {
            "verdict": "NO_VERIFICABLE",
            "reason": (
                "El snippet no dejó ningún token verificable (quedó vacío tras "
                "descartar palabras genéricas del lenguaje). No se consultó el "
                "catálogo: esto no dice nada sobre la construcción."
            ),
            "token": snippet,
        }

    # bt_api snippets are verified against the BtApi catalog, NOT GxSyntax.
    if language == "bt_api":
        return _verify_bt_api(snippet, candidate_token or token, version)

    session = get_session()
    try:
        query_token = candidate_token or token

        # 1. Si el receptor es una clase concreta (ej. Date.AddDays)
        if receiver and not receiver.startswith("&"):
            exacta_clase = (
                session.query(GxSyntax)
                .filter(
                    func.lower(GxSyntax.name) == query_token.lower(),
                    func.lower(GxSyntax.object_type) == receiver.lower(),
                )
                .first()
            )
            if exacta_clase:
                entry = exacta_clase
                _log_verdict("verify_syntax", snippet, "VÁLIDO", entry.id, version, entry.source)
                return {
                    "verdict": "VÁLIDO",
                    "name": entry.name,
                    "object_type": entry.object_type,
                    "signature": entry.signature,
                    "version": entry.version_since,
                    "source": entry.source,
                    "notes": f"Válido según {entry.source} ({entry.object_type}.{entry.name})",
                }

            # Si no está en esa clase, verificar si existe en otros tipos (Defecto 5.3)
            other_entries = (
                session.query(GxSyntax)
                .filter(func.lower(GxSyntax.name) == query_token.lower())
                .all()
            )
            if other_entries:
                other_types = sorted(list(set(e.object_type for e in other_entries)))
                _log_verdict("verify_syntax", snippet, "INVÁLIDO", None, version, None)
                return {
                    "verdict": "INVÁLIDO",
                    "reason": f"'{query_token}' no pertenece al tipo '{receiver}' (disponible en: {', '.join(other_types)}).",
                    "sugerencias": other_types,
                }

        # 2. Búsqueda con sufijos y miembros de llamada
        candidates_to_try = [query_token]
        for m in _member_tokens(snippet):
            if m not in candidates_to_try:
                candidates_to_try.append(m)

        matches = _lookup_entry(session, candidates_to_try, receiver_type=receiver)
        otras: list[str] = []
        entry = None
        if matches:
            entry = matches[0]
            otras = [m.name for m in matches[1:]]
        else:
            # Búsqueda exacta directa en name
            entry = session.query(GxSyntax).filter(func.lower(GxSyntax.name) == query_token.lower()).first()

        if entry:
            asked = _version_rank(version)
            since = _version_rank(entry.version_since)
            if since is not None and asked is not None and asked < since:
                _log_verdict("verify_syntax", snippet, "INVÁLIDO", entry.id, version, entry.source)
                return {
                    "verdict": "INVÁLIDO",
                    "name": entry.name,
                    "object_type": entry.object_type,
                    "version": entry.version_since,
                    "source": entry.source,
                    "reason": (
                        f"'{entry.name}' existe en el catálogo pero a partir de "
                        f"{entry.version_since}; se consultó por {version}, donde todavía no "
                        f"está disponible."
                    ),
                    "notes": (
                        "No lo uses en esta versión. Si necesitas el comportamiento, "
                        "busca la alternativa propia de la versión consultada."
                    ),
                }

            if since is None and asked is not None and asked < CATALOG_SOURCE_VERSION:
                _log_verdict("verify_syntax", snippet, "PISTA", entry.id, version, entry.source)
                return {
                    "verdict": "PISTA",
                    "name": entry.name,
                    "object_type": entry.object_type,
                    "signature": entry.signature,
                    "version": None,
                    "source": entry.source,
                    "reason": (
                        f"'{entry.name}' figura en el catálogo, que se nutre de fuentes "
                        f"gx{CATALOG_SOURCE_VERSION} y no trae columna de versión. "
                        f"Se consultó por {version}: el catálogo NO puede confirmarlo "
                        f"ahí."
                    ),
                    "notes": (
                        "No es un veredicto: es un indicio. Confírmalo contra la KB "
                        "de esa versión (MCP genexus16) o compilando, antes de usarlo."
                    ),
                }

            version_note = ""
            if version and not entry.version_since:
                version_note = (
                    f" OJO: el catálogo no registra versión para esta entrada, así que "
                    f"este veredicto no discrimina versiones — NO es evidencia de que la "
                    f"firma sea la de {version}. Confirmalo contra un objeto real de la KB."
                )
            elif version and version != entry.version_since:
                version_note = (
                    f" (versión pedida {version}; catálogo indica {entry.version_since})"
                )
            _log_verdict(
                "verify_syntax", snippet, "VÁLIDO", entry.id, version, entry.source
            )
            ambiguo = (
                f" Ojo: el catálogo tiene {len(otras) + 1} entradas para este miembro "
                f"({', '.join(otras)}); se devuelve la primera."
                if otras
                else ""
            )
            resultado = {
                "verdict": "VÁLIDO",
                "name": entry.name,
                "object_type": entry.object_type,
                "signature": entry.signature,
                "version": entry.version_since,
                "source": entry.source,
                "notes": (
                    f"Válido según {entry.source} ({entry.object_type})"
                    f"{version_note}{ambiguo}"
                ),
            }
            if otras:
                resultado["otras_entradas"] = otras
            return resultado

        # 3. Búsqueda por similaridad (tolerante: prefijo + difflib)
        target_for_similarity = candidate_token or token
        similar = _find_similar(session, target_for_similarity)

        if not similar:
            _log_verdict("verify_syntax", snippet, "NO_VERIFICABLE", None, version, None)
            _record_missing(target_for_similarity, None, version)
            return {
                "verdict": "NO_VERIFICABLE",
                "reason": "No encontrado en el catálogo. Verificar manualmente.",
                "token": target_for_similarity,
            }

        # Encontrado similar → INVÁLIDO
        _log_verdict("verify_syntax", snippet, "INVÁLIDO", None, version, None)
        return {
            "verdict": "INVÁLIDO",
            "reason": f"'{target_for_similarity}' no coincide exactamente con ninguna entrada del catálogo.",
            "sugerencias": similar[:5],
        }
    finally:
        session.close()


def list_methods(object_type: str, version: str | None = None) -> list[str]:
    """Lista las entradas catalogadas para un object_type."""
    session = get_session()
    query = session.query(GxSyntax.name).filter(GxSyntax.object_type == object_type)
    results = [r[0] for r in query.distinct().all()]
    session.close()
    return results


def list_object_types() -> list[str]:
    """Lista todos los object_type que existen en el catálogo."""
    session = get_session()
    query = session.query(GxSyntax.object_type).distinct().order_by(GxSyntax.object_type)
    results = [r[0] for r in query.all() if r[0]]
    session.close()
    return results


def get_signature(
    name: str, object_type: str, version: str | None = None
) -> dict | None:
    """Obtiene la firma exacta de un método/objeto."""
    session = get_session()
    query = session.query(GxSyntax).filter(
        func.lower(GxSyntax.name) == name.lower(),
        func.lower(GxSyntax.object_type) == object_type.lower(),
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
