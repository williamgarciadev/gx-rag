"""Verifica que un documento local quedó ingerido, y ENTERO.

Usage:
    python -m scripts.verify_ingest <ruta>... [--collection bt_docs]

`ingest_local` responde `OK — indexed=N` aunque haya perdido texto: el
2026-10-07 tres documentos entraron así sin una sección, una página o una línea.
Por cada archivo este script responde tres preguntas, cada una por una vía
independiente del pipeline que la produjo:

1. ¿Está en la base? Cuenta los chunks por la URI exacta que escribe el
   indexador (lectura determinista, no ranking).
2. ¿Qué páginas no cubre ningún capítulo? Las de índice se listan aparte: que
   falten es lo esperado.
3. ¿Qué palabras del PDF crudo no llegaron a lo extraído? Y qué líneas se
   descartaron como encabezado/pie en POCAS páginas: un pie real se repite en
   casi todas, así que una línea rara marcada como pie es sospechosa.

El texto crudo NO pasa por la lógica de índice del extractor, a propósito: un
verificador que comparte el código que verifica comparte sus errores. Probado
el 2026-10-07 contra el extractor previo al fix: marca la línea perdida de
ETP-TransferenciasACH, la referencia cruzada de MDI-10110 y la sección 1.0 de
Buenas Prácticas; contra el actual, solo queda "contenido" (el título del índice).

Sale con código 1 si algún archivo tiene 0 chunks. La pérdida de palabras no
cambia el código de salida: siempre hay algo de ruido y la juzga una persona.
"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from scripts._console import use_utf8_stdout
from src.ingestion.bt_pdf_extract import (
    extract_pages,
    is_furniture,
    is_toc_page,
)
from src.ingestion.local_loader import parse_local_docs

#: Tokens más cortos ("de", "la", "con") son ruido para esta señal.
_MIN_WORD_LEN = 4
_WORD_RE = re.compile(rf"\w{{{_MIN_WORD_LEN},}}")

#: Cuántas palabras perdidas y líneas sospechosas se muestran por archivo.
_REPORT_LIMIT = 15


def lost_words(raw: str, extracted: str) -> Counter:
    """Ocurrencias de cada palabra que el texto crudo tiene de más sobre lo extraído."""
    have = Counter(_WORD_RE.findall(extracted.lower()))
    want = Counter(_WORD_RE.findall(raw.lower()))
    return Counter({w: n - have[w] for w, n in want.items() if n > have[w]})


def uncovered_pages(page_ids: list[str], page_count: int) -> list[int]:
    """Páginas 2..N que ningún capítulo cita. La 1 es la portada y nunca se ingiere."""
    covered: set[int] = set()
    for page_id in page_ids:
        if not page_id:
            continue
        start, _, end = page_id.partition("-")
        covered.update(range(int(start), int(end or start) + 1))
    return [n for n in range(2, page_count + 1) if n not in covered]


_DIGITS_RE = re.compile(r"\d+")

#: Un pie real sale en casi todas las páginas; uno visto en menos de esta
#: fracción probablemente era contenido. "Una sola página" no alcanzaba: MDI-10110
#: repite la misma referencia cruzada en las págs. 15 y 23.
_FOOTER_MIN_SHARE = 0.5

# Copia DELIBERADA del criterio de puntos guía del extractor: si el verificador
# usara `content_after_index`, compartiría sus errores en vez de detectarlos.
_DOT_LEADER_RE = re.compile(r"\.{4,}\s*\d*\s*$")


def lonely_furniture(seen: dict[str, set[int]], page_count: int) -> list[str]:
    """Líneas descartadas como encabezado/pie en pocas páginas: candidatas a contenido.

    `seen` va de texto de línea a páginas donde se descartó. Los dígitos se
    ignoran al agrupar: "Página 10 de 80" y "Página 11 de 80" son el mismo pie.
    """
    pages_by_shape: dict[str, set[int]] = {}
    for text, pages in seen.items():
        pages_by_shape.setdefault(_DIGITS_RE.sub("#", text), set()).update(pages)
    threshold = max(1, (page_count - 1) * _FOOTER_MIN_SHARE)  # sin la portada
    return sorted(
        text
        for text in seen
        if len(text) > 3 and len(pages_by_shape[_DIGITS_RE.sub("#", text)]) < threshold
    )


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def source_patterns(path: Path) -> tuple[str, str]:
    """(uri exacta, patrón LIKE de sus capítulos '<uri>#ch=N').

    La URI lleva los espacios como '%20'; se escapa para que LIKE no los lea
    como comodines.
    """
    uri = path.resolve().as_uri()
    return uri, _like_escape(uri) + "#%"


def _count_chunks(conn, collection: str, path: Path) -> int:
    exact, chapters = source_patterns(path)
    row = conn.execute(
        "SELECT count(*) FROM chunks WHERE collection = %s "
        "AND (source = %s OR source LIKE %s ESCAPE '\\')",
        (collection, exact, chapters),
    ).fetchone()
    return row[0]


def _report_pdf(path: Path) -> None:
    docs = list(parse_local_docs([path]))
    pages = extract_pages(path)

    raw_parts: list[str] = []
    toc_pages: list[int] = []
    furniture_pages: dict[str, set[int]] = {}
    for page in pages[1:]:  # la 1 es la portada
        lines = [ln for ln in page.lines if not ln.rotated]
        for ln in lines:
            if is_furniture(ln, page.height):
                furniture_pages.setdefault(ln.text.strip(), set()).add(page.number)
        body = [ln for ln in lines if not is_furniture(ln, page.height)]
        if is_toc_page(body, page.width):
            toc_pages.append(page.number)
            body = [ln for ln in body if not _DOT_LEADER_RE.search(ln.text.strip())]
        raw_parts.extend(ln.text for ln in body)

    lost = lost_words("\n".join(raw_parts), "\n".join(d.content for d in docs))
    missing = [n for n in uncovered_pages([d.page_id for d in docs], len(pages)) if n not in toc_pages]
    lonely = lonely_furniture(furniture_pages, len(pages))

    print(f"  capítulos extraídos: {len(docs)} · páginas: {len(pages)} · índice: {toc_pages or '—'}")
    print(f"  páginas sin cubrir (fuera de portada e índice): {missing or 'ninguna'}")
    print(f"  palabras perdidas: {sum(lost.values())}", end="")
    print(f" → {lost.most_common(_REPORT_LIMIT)}" if lost else "")
    if lonely:
        print(f"  ⚠ descartadas como pie en pocas páginas ({len(lonely)}): revisar si eran contenido")
        for text in lonely[:_REPORT_LIMIT]:
            print(f"      · {text[:100]}")


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--collection", default="bt_docs")
    args = parser.parse_args(argv)

    from src.vectorstore.pg_client import close_pool, get_pool

    absent = 0
    try:
        with get_pool().connection() as conn:
            for path in args.paths:
                if not path.is_file():
                    print(f"\n✗ {path}: no existe")
                    absent += 1
                    continue
                n = _count_chunks(conn, args.collection, path)
                mark = "✓" if n else "✗"
                print(f"\n{mark} {path.name}\n  chunks en {args.collection}: {n}")
                absent += n == 0
                if path.suffix.lower() == ".pdf":
                    _report_pdf(path)
    finally:
        # Sin esto los hilos del pool tardan 5 s cada uno en morir al salir.
        close_pool()
    return 1 if absent else 0


if __name__ == "__main__":
    sys.exit(main())
