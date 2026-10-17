"""Arma un lote de descarga del portal SGR cruzando su inventario con lo que ya tiene el RAG.

Usage:
    python -m scripts.sgr_plan <inventario.jsonl> --solo "Manuales/BANTOTAL CORE  NÚCLEO/" \\
        [--solo <otro prefijo>] [--carpeta <carpeta>] [--out lote.json] [--collection bt_docs]

`--solo` reparte un plan deduplicado sobre todo el árbol: así un documento que está en dos
carpetas cae en un solo lote. Planear con `--carpeta` sola deduplica únicamente dentro de ella.

El inventario lo produce `sgr_docs.py inventario` (skill leer-doc-sgr-bantotal); el lote que
escribe este script lo consume `sgr_docs.py descargar`. Aquí vive la DECISIÓN de qué bajar,
con las reglas acordadas en docs/INGESTA-BANTOTAL.md:

- Solo localización Global (GL) y Colombia (CO); en una carpeta por país manda la carpeta.
- Un documento es código + título: el mismo código lo comparten documentos distintos.
- De un mismo documento se baja solo la versión más alta, comparada como número.
- Entra lo nuevo y la versión más nueva de lo ya ingerido. Lo demás se informa, no se baja.

Solo PDFs: las planillas y los .zip no pasan por el extractor del RAG.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from scripts._console import use_utf8_stdout
from src.collection_policy import add_collection_argument
from src.ingestion.bt_pdf_extract import DOC_CODE_PREFIXES

_SEP = "\\"
#: Código, localización opcional y versión. Tolera espacios: el portal publica "MDI-10211- GL-V3R1.1".
_CODE_RE = re.compile(
    rf"((?:{'|'.join(DOC_CODE_PREFIXES)})\s?-\s?\d{{4,6}})[\s-]*(?:([A-Z]{{2}})\s?-\s?)?"
    r"V(\d+)R(\d+)(?:\.(\d+))?",
    re.IGNORECASE,
)
#: Países que aparecen como carpeta en el inventario del 2026-10-07. Una carpeta con uno de estos
#: nombres, A CUALQUIER PROFUNDIDAD, decide la localización por encima del código del archivo:
#: el portal publica informes del BCU uruguayo con código -GL- bajo
#: "...Organismos Reguladores\Uruguay\..." (lote D1.4). Una carpeta que no está aquí no se
#: adivina ("MANUALES POR PAÍS\BANTOTAL CORE  NÚCLEO" tiene 128 manuales Globales): decide el código.
_COUNTRIES = {"argentina", "bolivia", "chile", "colombia", "el salvador", "guatemala", "honduras",
              "méxico", "panamá", "paraguay", "perú", "uruguay"}
_WANTED_SCOPES = {"GL", "CO"}
#: Solo dígitos entre paréntesis, al final: "(2)" es copia; "(PRICO001-02)" es parte del nombre.
_COPY_MARKER_RE = re.compile(r"\s*\(\d+\)$")
_ROOT_DIR = "WEBV3R1"
_WANTED_STATUS = {"nuevo", "versión nueva"}


@dataclass(frozen=True)
class Doc:
    link: str
    name: str


def doc_key(name: str) -> tuple[tuple[str, str] | None, tuple[int, ...] | None]:
    """((código, título), versión) del nombre de un archivo; (None, None) si no tiene código."""
    m = _CODE_RE.search(name)
    if not m:
        return None, None
    code = re.sub(r"\s", "", m.group(1)).upper()
    # " (2)" al final es el marcador de copia de Windows, no parte del título: sin quitarlo, una
    # copia idéntica cuenta como otro documento (le pasó a ETP-10114 SARC, lote D2.1).
    tail = _COPY_MARKER_RE.sub("", name[m.end():].lower().removesuffix(".pdf"))
    title = re.sub(r"\W", "", tail)
    return (code, title), tuple(int(g or 0) for g in m.groups()[2:])


def scope_of(link: str) -> str:
    """GL / CO / 'fuera' (otro país) / 'sin-país' (sin segmento o sin código)."""
    parts = link.split(_SEP)
    for folder in parts[:-1]:
        if folder.lower() in _COUNTRIES:
            return "CO" if folder.lower() == "colombia" else "fuera"
    m = _CODE_RE.search(parts[-1])
    if not m or not m.group(2):
        return "sin-país"
    loc = m.group(2).upper()
    return loc if loc in _WANTED_SCOPES else "fuera"


def latest_only(docs: list[Doc]) -> tuple[list[Doc], list[Doc]]:
    """(los que quedan, las versiones viejas descartadas). Sin código: quedan todos."""
    best: dict[object, tuple[Doc, tuple[int, ...]]] = {}
    for doc in docs:
        key, version = doc_key(doc.name)
        slot = key if key is not None else doc.link
        # Orden: versión más alta y, a igual versión, el nombre SIN marcador de copia " (2)".
        rank = (version or (), not _COPY_MARKER_RE.search(doc.name.removesuffix(".pdf")))
        if slot not in best or rank > best[slot][1]:
            best[slot] = (doc, rank)
    kept = [doc for doc, _ in best.values()]
    return kept, [d for d in docs if d not in kept]


def rel_path(link: str) -> str:
    """Ruta del archivo relativa a Bantotal V3R1, con '/': "Manuales/BANTOTAL CORE  NÚCLEO/..."."""
    return link.split(_ROOT_DIR + _SEP, 1)[-1].replace(_SEP, "/")


def split_lot(docs: list[Doc], prefix: str) -> list[Doc]:
    """La parte de un plan YA deduplicado que cae bajo `prefix` (relativo a Bantotal V3R1).

    Se deduplica sobre todo el árbol y recién después se reparte: planear carpeta por carpeta
    deja el mismo documento en dos lotes cuando está en dos carpetas.
    """
    return [d for d in docs if rel_path(d.link).startswith(prefix)]


def status_of(name: str, have: dict[tuple[str, str], set[tuple[int, ...]]]) -> str:
    """Estado del documento frente a lo que ya tiene el RAG."""
    key, version = doc_key(name)
    if key is None:
        return "sin código"
    if key not in have:
        return "nuevo"
    if version in have[key]:
        return "ya está"
    return "versión nueva" if version > max(have[key]) else "versión vieja"


def have_from_db(collection: str) -> dict[tuple[str, str], set[tuple[int, ...]]]:
    from src.vectorstore.pg_client import close_pool, get_pool

    have: dict[tuple[str, str], set[tuple[int, ...]]] = {}
    try:
        with get_pool().connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT split_part(source, '#', 1) FROM chunks WHERE collection = %s",
                (collection,),
            ).fetchall()
    finally:
        close_pool()
    for (source,) in rows:
        key, version = doc_key(unquote(source).replace("/", _SEP).split(_SEP)[-1])
        if key is not None:
            have.setdefault(key, set()).add(version)
    return have


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("inventario", type=Path)
    parser.add_argument("--carpeta", default=_ROOT_DIR,
                        help="carpeta del portal a planear (por defecto, todo Bantotal V3R1)")
    parser.add_argument("--solo", action="append", default=[], metavar="PREFIJO",
                        help="deja en el lote solo lo que está bajo este prefijo relativo a Bantotal "
                             "V3R1 (repetible). Se aplica DESPUÉS de deduplicar sobre --carpeta")
    parser.add_argument("--out", type=Path)
    add_collection_argument(parser, default="bt_docs")
    args = parser.parse_args(argv)

    marker = _SEP + args.carpeta + _SEP
    pdfs = []
    for line in args.inventario.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        if item.get("File") == "S" and item.get("Extension", "").lower() == ".pdf" and marker in item["Link"]:
            pdfs.append(Doc(link=item["Link"], name=item["Link"].split(_SEP)[-1]))
    if not pdfs:
        print(f"[ERROR] ningún PDF bajo la carpeta {args.carpeta!r} (¿nombre exacto, con sus dobles espacios?)")
        return 1

    have = have_from_db(args.collection)
    by_scope: dict[str, list[Doc]] = {}
    for doc in pdfs:
        by_scope.setdefault(scope_of(doc.link), []).append(doc)
    kept, older = latest_only(by_scope.get("GL", []) + by_scope.get("CO", []))
    if args.solo:
        kept = [d for prefix in args.solo for d in split_lot(kept, prefix)]

    lote, report = [], {}
    for doc in kept:
        st = status_of(doc.name, have)
        report.setdefault(st, []).append(doc.name)
        if st in _WANTED_STATUS:
            rel = doc.link.split(marker, 1)[1].replace(_SEP, "/")
            lote.append({"link": doc.link, "folder": doc.link.rsplit(_SEP, 1)[0], "rel": rel, "status": st})

    print(f"PDFs bajo {args.carpeta!r}: {len(pdfs)} · " + " · ".join(f"{k}={len(v)}" for k, v in sorted(by_scope.items())))
    for st in ("nuevo", "versión nueva", "ya está", "versión vieja", "sin código"):
        names = report.get(st, [])
        if names:
            print(f"\n[{st}] {len(names)}" + ("  → al lote" if st in _WANTED_STATUS else ""))
            for name in sorted(names):
                print(f"  {name}")
    for doc in older:
        print(f"[versión vieja dentro del portal, no se baja] {doc.name}")
    for doc in by_scope.get("sin-país", []):
        print(f"[sin país o sin código: decide el usuario] {doc.name}")
    if args.out:
        args.out.write_text(json.dumps(lote, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n[lote] {len(lote)} PDFs → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
