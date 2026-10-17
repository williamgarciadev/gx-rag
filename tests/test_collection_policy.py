"""La política de colecciones vive en un módulo, y todos la consultan ahí.

Antes estaba repartida en server.py, tool_requests.py, kb_loader.py (una copia
de KB_COLLECTION), config.py (un CollectionsConfig que nadie leía y que listaba
`cross_ref`) y doce CLIs que aceptaban cualquier nombre: un `--collection
gx_doc` llegaba al SQL y volvía como "0 chunks".
"""

import argparse
import re
from pathlib import Path

import pytest

from src import collection_policy as cp

pytestmark = pytest.mark.unit

_RAIZ = Path(__file__).resolve().parent.parent


def _parser(**kwargs) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    cp.add_collection_argument(p, **kwargs)
    return p


def test_el_cli_rechaza_una_coleccion_desconocida():
    with pytest.raises(SystemExit) as salida:
        _parser().parse_args(["--collection", "gx_doc"])
    assert salida.value.code == 2


def test_el_cli_acepta_las_conocidas_y_respeta_el_default():
    assert _parser().parse_args(["--collection", "bt_docs"]).collection == "bt_docs"
    assert _parser(default="bt_docs").parse_args([]).collection == "bt_docs"
    assert _parser(default=None).parse_args([]).collection is None


def test_ningun_cli_declara_collection_por_su_cuenta():
    """Un --collection declarado a mano vuelve a aceptar cualquier nombre."""
    a_mano = [
        f"{p.relative_to(_RAIZ)}"
        for p in sorted((_RAIZ / "scripts").glob("*.py"))
        if re.search(r"""add_argument\(\s*["']--collection["']""", p.read_text(encoding="utf-8"))
    ]
    assert not a_mano, f"Usa collection_policy.add_collection_argument en: {a_mano}"


def test_ningun_modulo_redefine_las_colecciones_de_la_politica():
    """Una copia de KB_COLLECTION vivía en kb_loader. `is` no lo detecta: Python
    interna los literales iguales, así que dos copias son el mismo objeto."""
    nombres = "KNOWN_COLLECTIONS|KB_COLLECTION|WRITABLE_COLLECTIONS|PUBLIC_COLLECTIONS|NON_PUBLIC_COLLECTIONS"
    copias = [
        f"{p.relative_to(_RAIZ)}"
        for p in sorted((_RAIZ / "src").rglob("*.py"))
        if p.name != "collection_policy.py"
        and re.search(rf"^({nombres})\s*=", p.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert not copias, f"Importa de collection_policy en vez de redefinir: {copias}"


def test_config_ya_no_declara_colecciones():
    """CollectionsConfig no lo leía nadie y mentía (cross_ref, sin gx_kb_objects)."""
    from src import config

    assert not hasattr(config, "CollectionsConfig")
    assert not hasattr(config.config, "collections")
