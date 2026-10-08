"""Busca credenciales, IPs internas y correos en documentos ANTES de indexarlos.

Usage:
    python -m scripts.scan_secrets <ruta>... | --list <archivo con una ruta por línea>

Es el paso 1 de cada lote de docs/PLAN-INGESTA-SGR.md. Lo que entra al RAG se lo
devuelve a cualquier consulta, en cualquier proyecto, así que una credencial real
del banco no puede pasar sin que una persona la vea.

INFORMATIVO, no bloquea: los manuales traen valores de ejemplo (`Password=xxxx`)
y se ingieren tal cual (decisión del usuario, ver docs/INGESTA-BANTOTAL.md). El
script muestra cada hallazgo con su texto para que la persona juzgue; siempre
sale con código 0 salvo que un archivo no se pueda leer.

Vive en el repo y no en el scratchpad porque el scratchpad se vació solo la noche
del 2026-10-07 y se llevó la versión anterior.
"""

import argparse
import re
import sys
from pathlib import Path

from scripts._console import use_utf8_stdout
from scripts._path_list import read_path_list
from src.ingestion.bt_pdf_extract import extract_pages

#: (tipo, patrón). Cada patrón exige un VALOR, no la mención del concepto: un manual
#: que explica "ingrese su contraseña" no tiene nada que revisar. Y el valor va en la
#: MISMA línea (espacio o tabulador, no \s): "Clave:" al final de una línea es un
#: rótulo de campo, y lo que sigue es el párrafo siguiente.
_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("password", re.compile(
        r"\b(?:password|passwd|pwd|contrase[ñn]a|clave)[ \t]*[:=][ \t]*\S+", re.IGNORECASE)),
    ("connection_string", re.compile(
        r"\b(?:jdbc:\w+://\S+|(?:server|data source)\s*=\s*[^;\s]+;[^\n]*(?:user|uid)\s*=)",
        re.IGNORECASE)),
    ("private_ip", re.compile(
        r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")),
    ("email", re.compile(r"\b[\w.+-]+@bancocontactar\.com\b", re.IGNORECASE)),
    ("token", re.compile(r"\b(?:api[_-]?key|token|secret)[ \t]*[:=][ \t]*[A-Za-z0-9+/_-]{16,}", re.IGNORECASE)),
)

#: Cuántos hallazgos se muestran por archivo; el total siempre se informa.
_REPORT_LIMIT = 10


def find_secrets(text: str) -> list[tuple[str, str]]:
    """[(tipo, texto encontrado)] en orden de aparición."""
    hallazgos = []
    for kind, pattern in _PATTERNS:
        hallazgos.extend((kind, m.group(0)) for m in pattern.finditer(text))
    return hallazgos


def _document_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return "\n".join(ln.text for page in extract_pages(path) for ln in page.lines)
    return path.read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--list", type=Path, help="archivo UTF-8 con una ruta por línea")
    args = parser.parse_args(argv)
    paths = list(args.paths)
    if args.list:
        paths += read_path_list(args.list)

    unreadable = con_hallazgos = 0
    for path in paths:
        try:
            hallazgos = find_secrets(_document_text(path))
        except Exception as e:  # un PDF roto no detiene el lote: se informa
            unreadable += 1
            print(f"✗ {path.name}: no se pudo leer ({e})")
            continue
        if hallazgos:
            con_hallazgos += 1
            print(f"⚠ {path.name}: {len(hallazgos)} hallazgo(s)")
            for kind, text in hallazgos[:_REPORT_LIMIT]:
                print(f"    [{kind}] {text[:120]}")
    print(f"\n{len(paths)} documento(s) · {con_hallazgos} con hallazgos · {unreadable} ilegible(s)")
    return 1 if unreadable else 0


if __name__ == "__main__":
    sys.exit(main())
