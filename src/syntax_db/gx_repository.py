"""
Repositorio GeneXus — operaciones contra la Syntax DB.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func

from src.syntax_db.models import (
    BtApi,
    GxSyntax,
    MissingMethod,
    ValidationLog,
    get_session,
)
from src.syntax_db.snippet_parser import ParsedSnippet, parse_snippet

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

# Sufijos de título que nombran un MIEMBRO de otra cosa (un command no lo es).
_MEMBER_SUFFIXES = ("method", "property", "event")


def _lookup_entry(session, nombres: tuple[str, ...], *, invoked_as_member: bool) -> list[GxSyntax]:
    """Entradas de gx_syntax que matchean alguno de `nombres`, mejor primero."""
    for base in nombres:
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

            # Los títulos de la wiki tienen dos formas, y solo esas valen:
            # «Miembro calificador» («Add method - SDT Collection»), y, si el
            # snippet invoca un MIEMBRO (&col.Add), «Dueño Miembro tipo»
            # («Business Component Add method»). Un LIKE '% n %' abierto daba
            # VÁLIDO a «Component» con «Refresh Web Component command»: una
            # palabra dentro del nombre de otro construct no es ese construct.
            forma = func.lower(GxSyntax.name).like(f"{n} %")
            if invoked_as_member:
                for sufijo in _MEMBER_SUFFIXES:
                    forma = forma | func.lower(GxSyntax.name).like(f"% {base.lower()} {sufijo}")
            calificadas = (
                session.query(GxSyntax)
                .filter(forma)
                .order_by(GxSyntax.name)
                .all()
            )
            if calificadas:
                return sorted(
                    calificadas,
                    key=lambda e: _INVOCABILITY.get((e.object_type or "").lower(), 99),
                )
    return []


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


# Parecido mínimo para llamar "typo" a un nombre. Un INVÁLIDO le dice al agente
# que busque el error de tipeo, así que solo vale con un parecido de verdad:
# 'Trmi'→'Trim' da 0,75 y 'AddDay'→'AddDays' 0,92, mientras 'Len'→'Length' da
# 0,67 y 'Abs'→'Abort' 0,50, que son OTRAS funciones. Con difflib a 0,4 y las
# reglas de subcadena, cualquier nombre no catalogado salía INVÁLIDO con cinco
# sugerencias sin relación (docs/AUDITORIA-2026-10-17.md, H4).
TYPO_SIMILARITY = 0.75


def _find_similar(session, token: str) -> list[str]:
    """Nombres del catálogo que parecen un typo de `token`, el más parecido primero."""
    t = token.lower()
    scored: list[tuple[float, str]] = []
    for (name,) in session.query(GxSyntax.name).distinct().all():
        # Contra el nombre entero y contra su primera palabra: el título de la
        # wiki 'AddYears method' tiene que parecerse a 'AddYear'.
        score = max(
            difflib.SequenceMatcher(None, t, form.lower()).ratio()
            for form in (name, name.split(" ", 1)[0])
        )
        if score >= TYPO_SIMILARITY:
            scored.append((score, name))
    scored.sort(key=lambda par: (-par[0], par[1]))
    return [name for _, name in scored]


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

    Tres pasos con dueños distintos: parse_snippet decide QUÉ nombres consultar
    (puro), _judge los juzga contra el catálogo (lee, no escribe), y acá se
    registra la telemetría UNA vez por veredicto.
    """
    parsed = parse_snippet(snippet)

    if not parsed.names:
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
        return _verify_bt_api(snippet, parsed.names[0], version)

    session = get_session()
    try:
        judgment = _judge(session, parsed, version)
    finally:
        session.close()

    _log_verdict(
        "verify_syntax", snippet, judgment.result["verdict"],
        judgment.entry_id, version, judgment.source,
    )
    if judgment.missing:
        _record_missing(judgment.missing, None, version)
    return judgment.result


@dataclass(frozen=True)
class _Judgment:
    """El veredicto y lo que la telemetría necesita registrar de él."""

    result: dict
    entry_id: int | None = None
    source: str | None = None
    missing: str | None = None  # token a encolar para curación


def _is_catalog_type(session, name: str) -> bool:
    """¿Es `name` un object_type del catálogo (Date, Character...)?

    Un receptor sin & puede ser un tipo (Date.AddDays) o un objeto de la KB
    (PJCCY657.Call): solo en el primer caso tiene sentido acusar que el miembro
    'no pertenece al tipo'.
    """
    return (
        session.query(GxSyntax.id)
        .filter(func.lower(GxSyntax.object_type) == name.lower())
        .first()
        is not None
    )


def _judge(session, parsed: ParsedSnippet, version: str | None) -> _Judgment:
    """Juzga los nombres leídos contra el catálogo. Lee la base, no escribe."""
    query_token = parsed.names[0]
    receiver = parsed.receiver
    receiver_type = (
        receiver
        if receiver and not receiver.startswith("&") and _is_catalog_type(session, receiver)
        else None
    )

    # 1. El receptor es un tipo del catálogo (ej. Date.AddDays)
    if receiver_type:
        exacta_clase = (
            session.query(GxSyntax)
            .filter(
                func.lower(GxSyntax.name) == query_token.lower(),
                func.lower(GxSyntax.object_type) == receiver_type.lower(),
            )
            .first()
        )
        if exacta_clase:
            # Por _judge_entry como cualquier otra entrada: un VÁLIDO propio de
            # este camino se saltaba el gate de versión (Date.AddDays con gx16
            # daba VÁLIDO; &d.AddDays, PISTA).
            return _judge_entry(exacta_clase, [], version)

        # Si no está en esa clase, verificar si existe en otros tipos (Defecto 5.3)
        other_entries = (
            session.query(GxSyntax)
            .filter(func.lower(GxSyntax.name) == query_token.lower())
            .all()
        )
        if other_entries:
            other_types = sorted(list(set(e.object_type for e in other_entries)))
            return _Judgment({
                "verdict": "INVÁLIDO",
                "reason": f"'{query_token}' no pertenece al tipo '{receiver_type}' (disponible en: {', '.join(other_types)}).",
                "sugerencias": other_types,
            })

    # 2. Búsqueda con sufijos sobre todos los nombres leídos
    matches = _lookup_entry(session, parsed.names, invoked_as_member=receiver is not None)
    if matches:
        return _judge_entry(matches[0], [m.name for m in matches[1:]], version)

    # 3. Búsqueda por similaridad (tolerante: prefijo + difflib)
    similar = _find_similar(session, query_token)

    if not similar:
        return _Judgment(
            {
                "verdict": "NO_VERIFICABLE",
                "reason": "No encontrado en el catálogo. Verificar manualmente.",
                "token": query_token,
            },
            missing=query_token,
        )

    # Encontrado similar → INVÁLIDO
    return _Judgment({
        "verdict": "INVÁLIDO",
        "reason": f"'{query_token}' no coincide exactamente con ninguna entrada del catálogo.",
        "sugerencias": similar[:5],
    })


def _judge_entry(entry: GxSyntax, otras: list[str], version: str | None) -> _Judgment:
    """Veredicto para una entrada encontrada: el gate de versión decide."""
    asked = _version_rank(version)
    since = _version_rank(entry.version_since)
    if since is not None and asked is not None and asked < since:
        return _Judgment(
            {
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
            },
            entry.id,
            entry.source,
        )

    if since is None and asked is not None and asked < CATALOG_SOURCE_VERSION:
        return _Judgment(
            {
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
            },
            entry.id,
            entry.source,
        )

    version_note = ""
    if version and not entry.version_since:
        version_note = (
            f" OJO: el catálogo no registra versión para esta entrada, así que "
            f"este veredicto no discrimina versiones — NO es evidencia de que la "
            f"firma sea la de {version}. Confírmalo contra un objeto real de la KB."
        )
    elif version and version != entry.version_since:
        version_note = (
            f" (versión pedida {version}; catálogo indica {entry.version_since})"
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
    return _Judgment(resultado, entry.id, entry.source)


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
