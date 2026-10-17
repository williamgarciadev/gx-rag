"""Saca de la base un documento reemplazado: respalda sus chunks y después los borra.

Usage:
    python -m scripts.retire_source <ruta-del-documento-viejo> \\
        [--replaced-by <ruta-del-nuevo>] [--collection bt_docs]

Reglas (cada una nació de un retiro hecho a mano el 2026-10-07):
- Si se indica `--replaced-by`, el nuevo TIENE que estar ya en la base: si no, no se borra nada.
- Un documento viejo con 0 chunks es un error, no un éxito: casi siempre es una ruta mal escrita.
- Se compara `source` por igualdad exacta, nunca con LIKE: la URI lleva '%20'.
- Se respalda ANTES de borrar, en data/backups/, con el embedding como texto (no con str(),
  que numpy resume con '...').

El manifiesto NO se toca aquí: marcar el viejo `ingest: false` es un paso aparte
(manifest_add.py de la skill rag-ingest), con la razón del reemplazo.
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from scripts._console import use_utf8_stdout
from src.collection_policy import add_collection_argument

_COUNT = "SELECT count(*) FROM chunks WHERE collection = %s AND source = %s"
_BACKUP = ("SELECT *, embedding::text AS embedding_text FROM chunks "
           "WHERE collection = %s AND source = %s")
_DELETE = "DELETE FROM chunks WHERE collection = %s AND source = %s"
#: Columnas que no se respaldan: `tsv` se regenera; `embedding` va como `embedding_text`.
_SKIP = {"tsv", "embedding"}

BACKUP_DIR = Path(__file__).resolve().parent.parent / "data" / "backups"


class RetireError(Exception):
    pass


@dataclass(frozen=True)
class Retired:
    deleted: int
    backup: Path


def _count(conn, collection, source):
    return conn.execute(_COUNT, (collection, source)).fetchone()[0]


def retire(conn, collection: str, source: str, *, replaced_by: str | None,
           backup_dir: Path = BACKUP_DIR) -> Retired:
    if replaced_by is not None and _count(conn, collection, replaced_by) == 0:
        raise RetireError(f"el reemplazo no está en la base, no se borra nada: {replaced_by}")
    expected = _count(conn, collection, source)
    if expected == 0:
        raise RetireError(f"0 chunks para {source}: ¿ruta mal escrita o ya retirado?")

    cursor = conn.execute(_BACKUP, (collection, source))
    rows = cursor.fetchall()
    columns = [d[0] for d in cursor.description]
    keep = [i for i, c in enumerate(columns) if c not in _SKIP]
    backup_dir.mkdir(parents=True, exist_ok=True)
    stem = unquote(source.rsplit("/", 1)[-1]).removesuffix(".pdf").removesuffix(".md")[:80]
    backup = backup_dir / f"backup-{stem}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    payload = [{columns[i]: (None if r[i] is None else str(r[i])) for i in keep} for r in rows]
    backup.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if len(payload) != expected:
        raise RetireError(f"el respaldo tiene {len(payload)} filas y se esperaban {expected}: no se borra")

    deleted = conn.execute(_DELETE, (collection, source)).rowcount
    if deleted != expected:
        raise RetireError(f"se borraron {deleted} filas y se esperaban {expected} (respaldo en {backup})")
    return Retired(deleted=deleted, backup=backup)


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("viejo", type=Path)
    parser.add_argument("--replaced-by", type=Path)
    add_collection_argument(parser, default="bt_docs")
    args = parser.parse_args(argv)

    from src.vectorstore.pg_client import close_pool, get_pool

    source = args.viejo.resolve().as_uri()
    replaced_by = args.replaced_by.resolve().as_uri() if args.replaced_by else None
    try:
        # Una transacción: si algo falla después del DELETE, no queda borrado a medias.
        with get_pool().connection() as conn, conn.transaction():
            result = retire(conn, args.collection, source, replaced_by=replaced_by)
    except RetireError as e:
        print(f"[NO SE RETIRÓ] {e}")
        return 1
    finally:
        close_pool()
    print(f"[retirado] {result.deleted} chunks de {args.viejo.name} · respaldo {result.backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
