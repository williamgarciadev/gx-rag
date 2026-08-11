"""
Repositorio GeneXus — operaciones contra la Syntax DB.
"""

import re
import logging
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


Verdict = Literal["VÁLIDO", "INVÁLIDO", "NO_VERIFICABLE"]


# Largo minimo de token para que la rama de similitud tenga derecho a acusar.
# Esa rama compara con `name LIKE '%<token[:8]>%'`, asi que con 1-2 caracteres el
# patron matchea cualquier entrada que los contenga ("Ad" pesca "AddYears", pero
# tambien "Load") y el INVALIDO que sale de ahi no es informacion: es ruido con
# forma de veredicto. Por debajo del piso la respuesta honesta es "no se".
MIN_SIMILARITY_TOKEN = 3


# La wiki titula cada pagina "<Nombre> <que es>": "AddYears method", "Printer
# rule". Nadie escribe eso en un procedure — escribe &fecha.AddYears(5). Estos
# sufijos son el puente entre el codigo real y el nombre del articulo.
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
# &var.ResourceName (acceso a propiedad, sin parentesis)
_MEMBER_ACCESS = re.compile(r"\.\s*([A-Za-z_][A-Za-z0-9_]*)")


def _member_tokens(snippet: str) -> list[str]:
    """Miembros invocados o accedidos en un snippet, del mas probable al menos.

    Las llamadas van primero: en "&a.B(&c.D())" lo que se esta verificando es
    normalmente la invocacion, no el objeto sobre el que se apoya.
    """
    vistos: list[str] = []
    for rx in (_MEMBER_CALL, _MEMBER_ACCESS):
        for m in rx.findall(snippet):
            if m not in vistos:
                vistos.append(m)
    return vistos


def _lookup_entry(session, nombres: list[str]) -> list:
    """Entradas de gx_syntax que matchean alguno de `nombres`, mejor primero.

    Prueba el nombre pelado y luego "<nombre> <sufijo>", sin distinguir
    mayusculas: el corpus mezcla "AddItem method" con firmas que escriben
    control.additem(...).

    Los titulos de la wiki no son uniformes: ademas de "AddYears method" hay
    "Add method - SDT Collection" y "Business Component Add method". Por eso,
    si el nombre exacto no aparece, se buscan las variantes calificadas.

    Devuelve una LISTA a proposito. Un mismo miembro puede existir con
    semanticas distintas (Add sobre coleccion de SDT vs sobre coleccion de BC);
    quedarse con la primera y callar el resto seria justo el tipo de respuesta
    con falsa autoridad que este catalogo existe para evitar.
    """
    for base in nombres:
        for nombre in [base] + [f"{base} {s}" for s in _ENTRY_SUFFIXES]:
            n = nombre.lower()
            exacta = (
                session.query(GxSyntax)
                .filter(func.lower(GxSyntax.name) == n)
                .all()
            )
            if exacta:
                return exacta
            # "Add method - SDT Collection" / "Business Component Add method".
            # El espacio en los patrones evita que "Add method" pesque
            # "AddRange method".
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
                return calificadas
    return []


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

    # Sin token no hay nada que verificar, y hay que cortar ACA. Mas abajo la
    # rama de similitud arma `name LIKE '%%'`, que matchea TODAS las filas: un
    # snippet de puras stopwords ("do", "if else") volvia como INVALIDO con cinco
    # sugerencias arbitrarias. Un veredicto negativo fabricado, justo en la tool
    # que existe para no fabricar veredictos.
    if not token:
        _log_verdict("verify_syntax", snippet, "NO_VERIFICABLE", None, version, None)
        return {
            "verdict": "NO_VERIFICABLE",
            "reason": (
                "El snippet no dejó ningún token verificable (quedó vacío tras "
                "descartar palabras genéricas del lenguaje). No se consultó el "
                "catálogo: esto no dice nada sobre la construcción."
            ),
            "token": token,
        }

    # bt_api snippets are verified against the BtApi catalog, NOT GxSyntax.
    if language == "bt_api":
        return _verify_bt_api(snippet, token, version)

    session = get_session()

    # Búsqueda exacta en nombre
    entry = session.query(GxSyntax).filter(GxSyntax.name == token).first()

    # El snippet suele ser código, no el título de un artículo. Se intenta con
    # los miembros invocados (&fecha.AddYears(5) → AddYears) y, si el snippet no
    # tiene punto, con el token pelado — así "AddYears" encuentra "AddYears
    # method" en vez de caer en la rama de similitud y responder INVÁLIDO.
    otras: list[str] = []
    if not entry:
        matches = _lookup_entry(session, _member_tokens(snippet) or [token])
        if matches:
            entry = matches[0]
            otras = [m.name for m in matches[1:]]

    if entry:
        # El veredicto NO esta gateado por version, y hay que DECIRLO donde el
        # agente concluye. version_since no tiene formato normalizado y, peor, la
        # unica via al catalogo (promote_candidate) lo escribe hardcodeado en None
        # (candidate_repository.py:94): medido, las 59 filas del catalogo real
        # estan en NULL. Callarlo convierte este VALIDO en una firma de version
        # indeterminada presentada como confirmada, que es una de las cuatro
        # formas de alucinar que define docs/gx-bt-rag-calidad-y-no-alucinacion.md.
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
        session.close()
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

    # Buscar por similaridad en nombre — solo si el token da para comparar.
    similar = (
        (
            session.query(GxSyntax)
            .filter(GxSyntax.name.like(f"%{token[:8]}%"))
            .limit(5)
            .all()
        )
        if len(token) >= MIN_SIMILARITY_TOKEN
        else []
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
    """Lista las entradas catalogadas para un object_type.

    `version` se acepta y NO se usa para filtrar, a propósito. Filtrar por
    `version_since` garantizaba una lista vacía: no es que los datos "hoy" estén
    en NULL, es que `promote_candidate` —la única vía al catálogo— lo escribe
    hardcodeado en None (candidate_repository.py:94). Medido sobre el catálogo
    real: 59 filas, las 59 en NULL. Y una lista vacía acá la lee el agente como
    "ese objeto no tiene métodos", así que el filtro producía exactamente la
    alucinación en negativo que este módulo existe para evitar.

    Cuando el catálogo tenga versión normalizada, esto vuelve a filtrar; hasta
    entonces la interfaz declara que el parámetro no discrimina.
    """
    session = get_session()
    query = session.query(GxSyntax.name).filter(GxSyntax.object_type == object_type)
    results = [r[0] for r in query.distinct().all()]
    session.close()
    return results


def list_object_types() -> list[str]:
    """Los object_type que EXISTEN en el catálogo, ordenados.

    Alimenta la respuesta vacía de `list_methods`: sin esto, la tool sugería
    "Transaction" y "Procedure", que no tienen ni una fila (medido: Method 34,
    Event 9, Command 8, Rule 6, Property 2). Un ejemplo que garantiza cero
    resultados es peor que ningún ejemplo — manda al agente a concluir que el
    catálogo está vacío cuando lo que estaba mal era la taxonomía sugerida.
    """
    session = get_session()
    rows = (
        session.query(GxSyntax.object_type)
        .filter(GxSyntax.object_type.isnot(None))
        .distinct()
        .order_by(GxSyntax.object_type)
        .all()
    )
    session.close()
    return [r[0] for r in rows]


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
