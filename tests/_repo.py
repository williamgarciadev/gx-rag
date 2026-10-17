"""Archivos del repo para los tests que escanean el código fuente.

Los tests de registro (voseo), de política de colecciones y de imports recorren
los mismos .py con las mismas exclusiones. Viven acá para que una exclusión
nueva se agregue en un solo lugar.
"""

from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Fuera del escaneo: caché de Python y el piloto de video, que no es código del
# producto ni se mantiene al día con él.
_EXCLUIDAS = frozenset({"__pycache__", "video_pilot"})


def archivos_py(*carpetas: str) -> list[Path]:
    """Los .py bajo `carpetas` (relativas a la raíz), recursivo y ordenado."""
    return sorted(
        p
        for carpeta in carpetas
        for p in (RAIZ / carpeta).rglob("*.py")
        if not _EXCLUIDAS.intersection(p.parts)
    )
