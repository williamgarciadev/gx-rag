"""Trae al árbol de docs los cursos exportados del campus Bantotal (Moodle).

Usage:
    python -m scripts.import_campus <zip> <destino> [--dry-run]

El zip trae `bantotal-rag/corpus_md/<curso>/<actividad>.md`, cada uno con un frontmatter
plano (`tipo`, `estado`, `archivo`…). Copia a `<destino>/<curso>/` lo que debe entrar,
deja `<destino>/_inventario.tsv` con la decisión y el motivo de TODOS los archivos, y
`<destino>/_lote.txt` con las rutas copiadas, que es la lista que leen scan_secrets,
ingest_lote y verify_ingest.

Lo que queda fuera, y por qué:

- **Estado no `completo`**: un texto parcial es peor que un hueco (criterio del export).
- **Manuales V2R3 con versión V3R1 en `bt_docs`**: si conviven, el top-k mezcla versiones.
- **Material que ya entró por otra vía** (videos procesados localmente).
- **El mismo contenido en dos cursos**: entra una sola copia.

Los nombres del zip vienen en Unicode NFD; se escriben en NFC porque `chunks.source` se
compara byte a byte y una consulta tecleada siempre llega en NFC.
"""

import argparse
import hashlib
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path

# El mismo parser con que el indexador lee el frontmatter: si cada uno leyera distinto,
# se decidiría con un `estado` que no es el que termina en los metadatos del chunk.
from src.ingestion.local_loader import parse_frontmatter

CORPUS_PREFIX = "corpus_md/"

#: Bit 11 de la cabecera zip: el nombre está en UTF-8. Sin él, zipfile lo lee como cp437.
UTF8_FLAG = 0x800

#: Series de manual cuya versión V3R1 ya está en bt_docs (portal SGR). Medido el
#: 2026-10-10 contando chunks por `source`.
SUPERSEDED_CODES = (
    "MDU-10108", "MDI-10108", "MDU-10109", "MDI-10109", "MDU-10110", "MDU-10113",
    "MDU-10115", "MDU-10201", "MDI-10201", "MDI-10207", "MDU-10304", "MDI-10304",
    "MDU-10102", "ETP-10102", "ETP-10110", "MDI-10401",
)

#: PDF original (campo `archivo`) → por qué no entra aunque no tenga código de serie.
ALREADY_IN_PDF = {
    "Manual de Usuario_Cadena de Cierre.pdf": "ya está como MDU-10109-GL-V3R1.11 - Cadena de Cierre",
    "Consideraciones para RTE en Ingreso.pdf": "ya está en bt_docs con el mismo nombre",
    "Consideraciones para RTE en Anulación.pdf": "ya está en bt_docs con el mismo nombre",
}

#: Inventario de tablas MDU-99000 (el zip trae V2R3.4 y V2R3.7): ~2.000 chunks de una
#: versión anterior al core de Contactar (V3R1.11), que ensuciarían el top-k de toda
#: pregunta por una tabla. Decisión del usuario, 2026-10-10.
TABLE_INVENTORY = "Inventario de Tablas"
TABLE_INVENTORY_REASON = ("inventario de tablas V2R3: para tablas manda la base viva (MCP bantotal-dev) "
                          "y la skill bantotal_model_docs trae el MDU-99000 de la versión correcta")

#: Ruta en el zip (NFC; un prefijo que termina en "/" cubre la carpeta) → por qué no entra.
ALREADY_IN_PATH = {
    "buenas_prácticas_de_programación/": "ya está en bt_docs desde data/videos_processed",
    "contactar/08_video_060421_clientesycontabilidad.md":
        "ya está en bt_docs como curso-contabilidad-clientes-y-contabilidad-2021-04-06.md",
    "sistema_transaccional/12_video_definición_de_transacciones_ejercicio1_partea.md":
        "ya está en bt_docs como definicion-de-transacciones-ejercicio1-parte-a.md",
}

OUT_REASON = {
    "pendiente": "sin contenido aún (transcripción pendiente)",
    "sin_audio": "video sin voz: no hay texto que citar",
    "sin_contenido": "la actividad no tiene texto extraíble",
    "no_disponible": "la fuente no se pudo obtener",
    "incompleto": "contenido parcial: un texto truncado es peor que un hueco",
}


@dataclass
class Item:
    member: str  # nombre dentro del zip, tal cual (NFD)
    rel: str  # <curso>/<archivo>.md en NFC
    ingest: bool
    reason: str


def member_rel(info: zipfile.ZipInfo) -> str:
    """`<curso>/<archivo>.md` en NFC, recuperando nombres guardados sin el flag UTF-8."""
    name = info.filename
    if not info.flag_bits & UTF8_FLAG:
        try:
            name = name.encode("cp437").decode("utf-8")
        except UnicodeError:
            pass  # era cp437 de verdad: se deja como vino
    return unicodedata.normalize("NFC", name.split(CORPUS_PREFIX, 1)[1])


def _content_digest(text: str) -> str:
    """Huella del contenido sin frontmatter ni citas: el bloque `> curso › sección` cambia
    con el curso que contiene la actividad, y el mismo video aparece en varios."""
    body = text.split("\n---", 1)[1] if text.startswith("---") else text
    lines = [line for line in body.splitlines() if line.strip() and not line.startswith(">")]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _already_in(rel: str, archivo: str) -> str | None:
    for path, why in ALREADY_IN_PATH.items():
        if rel == path or (path.endswith("/") and rel.startswith(path) and not rel.endswith("/_curso.md")):
            return why
    if archivo in ALREADY_IN_PDF:
        return ALREADY_IN_PDF[archivo]
    if TABLE_INVENTORY in archivo:
        return TABLE_INVENTORY_REASON
    if any(archivo.startswith(code) for code in SUPERSEDED_CODES):
        return "versión V2R3: bt_docs ya tiene la V3R1 del portal SGR"
    return None


def decide(rel: str, fm: dict) -> tuple[bool, str]:
    """¿Entra este archivo? Devuelve la decisión y su motivo, que va al manifiesto."""
    if rel.endswith("/_curso.md"):
        return True, "Índice del curso del campus Bantotal: descripción y estado de cada actividad."
    estado = fm.get("estado", "")
    if estado != "completo":
        return False, OUT_REASON.get(estado, f"estado '{estado}' no reconocido")
    archivo = unicodedata.normalize("NFC", str(fm.get("archivo", "")))
    why = _already_in(rel, archivo)
    if why:
        return False, why
    return True, f"Campus Bantotal: {fm.get('tipo', '?')} completo, confianza {fm.get('confianza', '?')}."


def plan(z: zipfile.ZipFile) -> list[Item]:
    """Decide cada .md del zip, en orden, quitando el contenido repetido entre cursos."""
    items, seen = [], {}
    for info in sorted(z.infolist(), key=lambda i: i.filename):
        member = info.filename
        if not member.endswith(".md") or CORPUS_PREFIX not in member:
            continue
        rel = member_rel(info)
        text = z.read(info).decode("utf-8")
        ingest, reason = decide(rel, parse_frontmatter(text))
        if ingest and not rel.endswith("/_curso.md"):
            digest = _content_digest(text)
            if digest in seen:
                ingest, reason = False, f"mismo contenido que {seen[digest]}"
            else:
                seen[digest] = rel
        items.append(Item(member, rel, ingest, reason))
    return items


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("zip", type=Path)
    parser.add_argument("destino", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    with zipfile.ZipFile(args.zip) as z:
        items = plan(z)
        entran = [i for i in items if i.ingest]
        print(f"{len(items)} archivos | entran {len(entran)} | fuera {len(items) - len(entran)}")
        if args.dry_run:
            for i in items:
                print(("+ " if i.ingest else "- ") + f"{i.rel}  [{i.reason}]")
            return 0
        for i in entran:
            dest = args.destino / i.rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(z.read(i.member))

    (args.destino / "_lote.txt").write_text(
        "".join(f"{(args.destino / i.rel).as_posix()}\n" for i in entran), encoding="utf-8")
    (args.destino / "_inventario.tsv").write_text(
        "archivo\tentra\tmotivo\n" + "".join(f"{i.rel}\t{'sí' if i.ingest else 'no'}\t{i.reason}\n" for i in items),
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
