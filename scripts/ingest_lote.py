"""Ingiere un lote de docs/PLAN-INGESTA-SGR.md sin ahogar el PC.

Usage:
    python -m scripts.ingest_lote <lista.txt> [--threads 4] [--collection bt_docs] [--dry-run]

Lanza `scripts.ingest_local` sobre las rutas de una lista UTF-8 (una por línea) con:

- **Menos hilos** (`OMP_NUM_THREADS`/`MKL_NUM_THREADS`, por defecto 4 de los 8 de esta
  máquina): torch usa todos los núcleos si no se le dice nada, y la máquina deja de
  responder durante el lote.
- **Prioridad por debajo de lo normal** en Windows: si el usuario abre otra cosa, gana él.

Las rutas viajan desde un archivo y no desde la consola a propósito: impresas en
cp1252, las tildes de "NÚCLEO" llegaban rotas y el script recibía rutas que "no
existen" (2026-10-07).
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from scripts._path_list import read_path_list

#: Medido: 8 núcleos lógicos. La mitad deja la máquina usable a costa de ~1,5x de tiempo.
DEFAULT_THREADS = 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("lista", type=Path)
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--collection", default="bt_docs")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    paths = [str(p) for p in read_path_list(args.lista)]
    hilos = str(args.threads)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "OMP_NUM_THREADS": hilos, "MKL_NUM_THREADS": hilos}
    cmd = [sys.executable, "-m", "scripts.ingest_local", *paths, "--collection", args.collection]
    if args.dry_run:
        cmd.append("--dry-run")
    flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)  # 0 fuera de Windows
    return subprocess.call(cmd, env=env, creationflags=flags)


if __name__ == "__main__":
    sys.exit(main())
