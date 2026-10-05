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


Verdict = Literal["VÁLIDO", "INVÁLIDO", "NO_VERIFICABLE", "PISTA"]

# The GeneXus version the catalog's rows are DOCUMENTED IN — distinct from
# `version_since`, which says when a name first appeared. Sources are GX18
# (wiki, api.tsv), and none of them carry a version column, so almost every row
# lands with version_since = NULL.
#
# Treating that absence as "do not gate" answers VÁLIDO to a GX16 question, and
# that is a FALSE VALID: worse than a false positive because nothing surfaces
# it — the verdict looks authoritative and the code simply fails to compile
# later (`Abs()` exists in GX18, raises src0294 in GX16). The Bantotal KBs that
# consume this are GX16, so it is the common case, not the edge case.
CATALOG_SOURCE_VERSION = 18

# Preference order when one name exists under several object types. A snippet
# handed to verify_syntax is code being CALLED, so the invocable meaning wins;
# a property or event of the same name is the coincidence, not the answer.
_INVOCABILITY = {
    "command": 0,
    "function": 1,
    "method": 2,
    "data type": 3,
    "event": 4,
    "property": 5,
}

# Below this length a fuzzy lookup matches most of the catalog, so its
# "suggestions" are noise dressed as guidance.
_MIN_FUZZY_TOKEN = 3
# Prefix width for the near-miss lookup. A PREFIX, not a substring: the old
# `name LIKE '%{token}%'` only found catalog entries CONTAINING the query, so a
# typo that adds a character ("Formatt") matched nothing at all.
_FUZZY_PREFIX = 4


# Un método invocado sobre un receptor: `&Texto.Length()` → Length.
_METHOD_CALL_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")
# Una función invocada: `Trim(&Variable)` → Trim. El lookbehind excluye lo que
# ya capturó el patrón de método (no queremos el receptor ni el `.Nombre`).
_FUNCTION_CALL_RE = re.compile(r"(?<![.\w])([A-Za-z_][A-Za-z0-9_]*)\s*\(")

# Palabras del lenguaje que nunca son el nombre consultado.
_STOP = {
    "if", "else", "for", "while", "do", "return", "var", "let", "const",
    "function", "class", "import", "export", "new", "this", "self",
    "true", "false", "null", "none", "async", "await", "def", "in", "or", "and",
}


def _normalize_token(snippet: str) -> str:
    """Reduce un snippet AL NOMBRE QUE SE ESTÁ CONSULTANDO.

    `GxSyntax.name` guarda un nombre de una sola palabra, así que el token tiene
    que ser eso mismo o la búsqueda exacta no puede acertar nunca.

    La versión anterior unía TODAS las palabras del snippet con espacios
    (`Trim(&Variable)` → `"Trim Variable"`), de modo que ninguna consulta con
    argumentos podía igualar una fila del catálogo: `verify_syntax` devolvía
    NO_VERIFICABLE siempre, con catálogo lleno o vacío, y la capa que debía
    CORTAR la alucinación quedaba inerte.

    Orden de resolución, del más específico al más laxo:
      1. método sobre receptor  `&Texto.Length()` → Length
      2. llamada a función      `Trim(&Variable)` → Trim
      3. identificador suelto   `Len`             → Len
    """
    m = _METHOD_CALL_RE.search(snippet)
    if m:
        return m.group(1)

    for name in _FUNCTION_CALL_RE.findall(snippet):
        if name.lower() not in _STOP:
            return name

    for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", snippet):
        if tok.lower() not in _STOP:
            return tok

    return ""


def _version_rank(value: str | None) -> int | None:
    """'gx16' → 16, 'GX18 U5' → 18, None/basura → None.

    `version_since` es texto libre (String(50)) y nadie garantiza su formato, así
    que se extrae el primer número en vez de exigir una forma canónica. Devolver
    None cuando no se puede leer es deliberado: sin dato NO se filtra (ver
    `verify_syntax`)."""
    if not value:
        return None
    m = re.search(r"\d+", str(value))
    return int(m.group()) if m else None


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

    # Búsqueda exacta en nombre. Un mismo nombre vive bajo varios object_type
    # ("Call" es command en api.tsv y property en properties.tsv), así que sin
    # un orden explícito `.first()` devolvería la fila que se insertó primero:
    # el veredicto dependería del orden de carga del catálogo. Lo que se verifica
    # es código que se INVOCA, de modo que gana la fila invocable.
    entry = min(
        session.query(GxSyntax).filter(GxSyntax.name == token).all(),
        key=lambda e: _INVOCABILITY.get(e.object_type, len(_INVOCABILITY)),
        default=None,
    )

    if entry:
        # Copiar antes de cerrar la sesión: con el objeto detached, tocar un
        # atributo lanzaría DetachedInstanceError.
        e_id, e_name = entry.id, entry.name
        e_type, e_sig = entry.object_type, entry.signature
        e_since, e_source = entry.version_since, entry.source
        session.close()

        # ── Gate de versión ──────────────────────────────────────────────────
        # `version_since` significa "existe A PARTIR de esta versión". Si el
        # llamador pregunta por una ANTERIOR, el nombre todavía no existía ahí:
        # responder VÁLIDO sería un FALSO VÁLIDO, y eso es peor que un falso
        # positivo porque nada lo delata — el código simplemente no compila
        # después. Caso real: `Abs()` existe en GX18 y en GX16 da
        # `src0294 Unknown function`.
        #
        # Importa porque el catálogo se nutre de fuentes GX18 mientras las KB
        # Bantotal que lo consultan son GX16.
        asked = _version_rank(version)
        since = _version_rank(e_since)
        if asked is not None and since is not None and asked < since:
            _log_verdict("verify_syntax", snippet, "INVÁLIDO", e_id, version, e_source)
            return {
                "verdict": "INVÁLIDO",
                "name": e_name,
                "object_type": e_type,
                "version": e_since,
                "source": e_source,
                "reason": (
                    f"'{e_name}' existe en el catálogo pero a partir de "
                    f"{e_since}; se consultó por {version}, donde todavía no "
                    f"está disponible."
                ),
                "notes": (
                    "No lo uses en esta versión. Si necesitas el comportamiento, "
                    "busca la alternativa propia de la versión consultada."
                ),
            }

        # ── Sin `version_since`: el catálogo solo prueba SU propia versión ───
        # La fila existe, pero nada dice desde cuándo. Lo único demostrable es
        # que existe en la versión de la que salió el catálogo. Si preguntan por
        # una ANTERIOR, responder VÁLIDO sería inventar; responder INVÁLIDO
        # también, porque muchísimos nombres GX18 sí existen en GX16 (Trim,
        # Format). El estado honesto es el tercero: PISTA, que es la palabra que
        # usa la regla del proyecto — veredicto para GX18, indicio para GX16.
        if since is None and asked is not None and asked < CATALOG_SOURCE_VERSION:
            _log_verdict("verify_syntax", snippet, "PISTA", e_id, version, e_source)
            return {
                "verdict": "PISTA",
                "name": e_name,
                "object_type": e_type,
                "signature": e_sig,
                "version": None,
                "source": e_source,
                "reason": (
                    f"'{e_name}' figura en el catálogo, que se nutre de fuentes "
                    f"gx{CATALOG_SOURCE_VERSION} y no trae columna de versión. "
                    f"Se consultó por {version}: el catálogo NO puede confirmarlo "
                    f"ahí."
                ),
                "notes": (
                    "No es un veredicto: es un indicio. Confírmalo contra la KB "
                    "de esa versión (MCP genexus16) o compilando, antes de usarlo."
                ),
            }

        _log_verdict("verify_syntax", snippet, "VÁLIDO", e_id, version, e_source)
        return {
            "verdict": "VÁLIDO",
            "name": e_name,
            "object_type": e_type,
            "signature": e_sig,
            "version": e_since,
            "source": e_source,
            "notes": f"Válido según {e_source} ({e_type})",
        }

    # ── Near-miss: sugerencias para un typo ──────────────────────────────────
    # Un token corto casa con medio catálogo, así que sus "sugerencias" serían
    # ruido con aspecto de guía: mejor decir NO_VERIFICABLE.
    similar = []
    if len(token) >= _MIN_FUZZY_TOKEN:
        # PREFIJO, no subcadena. `name LIKE '%Formatt%'` no encuentra 'Format':
        # buscaba entradas que CONTUVIERAN la consulta, de modo que cualquier
        # typo que AGREGA una letra no devolvía nada — justo el typo más común.
        similar = (
            session.query(GxSyntax)
            .filter(GxSyntax.name.like(f"{token[:_FUZZY_PREFIX]}%"))
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


def _available_in(version_since: str | None, asked: int | None) -> bool:
    """Whether a row is available in the asked version.

    `version_since` means "exists FROM here onwards", so a method introduced in
    GX17 IS available in GX18. Filtering with `version_since == version` — as
    this module used to — reads the same column as "exists ONLY in", giving one
    contract two opposite meanings inside one file: `verify_syntax` gated by
    range while `list_methods` gated by string equality and returned nothing.

    A row with no version data is KEPT: most rows have none, and dropping them
    would empty the catalog. Whether that absence is safe to trust is
    `verify_syntax`'s call, and it answers PISTA.
    """
    if asked is None:
        return True
    since = _version_rank(version_since)
    return since is None or since <= asked


def list_methods(object_type: str, version: str | None = None) -> list[str]:
    """Lista todos los métodos de un tipo de objeto disponibles en `version`."""
    session = get_session()
    rows = (
        session.query(GxSyntax.name, GxSyntax.version_since)
        .filter(GxSyntax.object_type == object_type)
        .distinct()
        .all()
    )
    session.close()
    asked = _version_rank(version)
    return [name for name, since in rows if _available_in(since, asked)]


def get_signature(
    name: str, object_type: str, version: str | None = None
) -> dict | None:
    """Obtiene la firma exacta de un método/objeto."""
    session = get_session()
    entry = (
        session.query(GxSyntax)
        .filter(GxSyntax.name == name, GxSyntax.object_type == object_type)
        .first()
    )
    session.close()
    # Same 'since' semantics as list_methods — not string equality.
    if not entry or not _available_in(entry.version_since, _version_rank(version)):
        return None
    return {
        "name": entry.name,
        "object_type": entry.object_type,
        "signature": entry.signature,
        "description": entry.description,
        "version": entry.version_since,
        "source": entry.source,
    }
