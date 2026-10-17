"""Todo import de src.* o scripts.* apunta a un módulo que existe.

Tres CLIs (purge_collection, split_collection, ab_e5_prefix) quedaron
importando src.vectorstore.chroma_client después de la migración a Postgres y
nadie se enteró: un script que nadie corre no falla en ningún lado. Esto lo
detecta sin ejecutar nada (lee el AST), así que no carga modelos ni toca la base.
"""

import ast
from pathlib import Path

import pytest

from tests._repo import RAIZ as _RAIZ
from tests._repo import archivos_py

pytestmark = pytest.mark.unit

_PAQUETES = ("src", "scripts")


def _existe(modulo: str) -> bool:
    ruta = _RAIZ.joinpath(*modulo.split("."))
    return ruta.with_suffix(".py").is_file() or (ruta / "__init__.py").is_file() or ruta.is_dir()


def _imports_propios(archivo: Path):
    arbol = ast.parse(archivo.read_text(encoding="utf-8"))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.level == 0:
            if nodo.module.split(".")[0] in _PAQUETES:
                yield nodo.lineno, nodo.module
        elif isinstance(nodo, ast.Import):
            for alias in nodo.names:
                if alias.name.split(".")[0] in _PAQUETES:
                    yield nodo.lineno, alias.name


def test_ningun_import_propio_apunta_a_un_modulo_que_no_existe():
    rotos = [
        f"{archivo.relative_to(_RAIZ)}:{linea}: {modulo}"
        for archivo in archivos_py(*_PAQUETES)
        for linea, modulo in _imports_propios(archivo)
        if not _existe(modulo)
    ]
    assert not rotos, "Imports a módulos inexistentes:\n" + "\n".join(rotos)
