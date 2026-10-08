"""Lista de rutas de un lote: un archivo UTF-8 con una ruta por línea.

Las rutas viajan en un archivo y no por la consola porque, impresas en cp1252, las
tildes de "NÚCLEO" o "PAÍS" llegaban rotas y los scripts recibían rutas que "no
existen" (2026-10-07). La leen scan_secrets, ingest_lote y verify_ingest: una sola
función para que los tres pasos de un lote trabajen sobre el mismo conjunto.
"""

from pathlib import Path


def read_path_list(lista: Path) -> list[Path]:
    """Rutas del archivo, en orden, sin líneas vacías ni espacios alrededor."""
    lines = lista.read_text(encoding="utf-8").splitlines()
    return [Path(line.strip()) for line in lines if line.strip()]
