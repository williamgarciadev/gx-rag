"""
Lectura de un snippet GeneXus: qué nombres hay que consultar en el catálogo.

Es la mitad pura de verify_syntax: no toca la base. La otra mitad (juzgar esos
nombres contra el catálogo) vive en gx_repository.

El contrato que importa es negativo: de acá no puede salir un nombre que el
agente NO escribió. verify_syntax trata un nombre que no matchea exacto como un
posible typo y responde INVÁLIDO, así que un nombre fabricado por la lectura
("x Trim y", "Revise" sacado de un literal, "Each Customer") se convierte en una
acusación falsa contra código válido (docs/AUDITORIA-2026-10-17.md, H1-H3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"

# Literales y comentarios se vacían ANTES de leer: un punto o un paréntesis
# dentro de ellos no es sintaxis. Se reemplazan por '' (y no se borran) para que
# 'Msg('a.b')' siga siendo una llamada a Msg.
_LITERAL_OR_COMMENT = re.compile(
    r"'[^']*'|\"[^\"]*\"|//[^\n]*|/\*.*?\*/", re.DOTALL
)

# receptor.miembro. El receptor arranca con & o letra, nunca con dígito (1.5 no
# es un acceso a miembro), y no puede ser la cola de otro identificador.
_MEMBER = re.compile(rf"(?<![\w&])(&?{_IDENT})\s*\.\s*({_IDENT})")

# Ident( que no sea miembro (.Ident() ni variable (&Ident().
_CALL = re.compile(rf"(?<![\w&.])({_IDENT})\s*\(")

# Palabras del lenguaje que NO son nombres consultables. Si aparecen con
# paréntesis (If(...)) siguen sin serlo, y si abren el snippet sin llamadas
# ('For Each Customer') el snippet es una sentencia, no un nombre suelto.
_CONTROL = frozenset({
    "if", "else", "elseif", "endif", "for", "each", "endfor", "in", "where",
    "do", "while", "enddo", "case", "otherwise", "endcase", "return", "and",
    "or", "not", "true", "false", "null", "new", "endnew", "sub", "endsub",
    "event", "endevent", "when", "defined", "by", "order", "then", "exit",
})


@dataclass(frozen=True)
class ParsedSnippet:
    """receiver: lo que está a la izquierda del primer '.miembro' ('&t', 'Date',
    'PJCCY657'), o None. Que sea un TIPO lo decide quien lo juzga, no esto.
    names: nombres a consultar, del más específico al menos. Vacío = nada que
    verificar, que es distinto de 'inválido'."""

    receiver: str | None
    names: tuple[str, ...]


def parse_snippet(snippet: str) -> ParsedSnippet:
    code = _LITERAL_OR_COMMENT.sub("''", snippet or "")

    members = _MEMBER.findall(code)
    receiver = members[0][0] if members else None
    names: list[str] = []
    for _, member in members:
        _add(names, member)
    for call in _CALL.findall(code):
        if call.lower() not in _CONTROL:
            _add(names, call)
    if names:
        return ParsedSnippet(receiver, tuple(names))

    # Sin llamada ni miembro: el snippet entero es el nombre ('Len', o un título
    # de la wiki como 'AddYears method'), salvo que sea una sentencia.
    words = re.findall(_IDENT, code)
    if not words or words[0].lower() in _CONTROL:
        return ParsedSnippet(None, ())
    return ParsedSnippet(None, (" ".join(words),))


def _add(names: list[str], name: str) -> None:
    if name not in names:
        names.append(name)
