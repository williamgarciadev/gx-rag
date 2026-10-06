"""Lógica pura de scripts/bootstrap_kb.py: el selector y la ficha mecánica.

Tests unitarios sin Postgres ni MCP. Lo que queda fuera a propósito es el bucle de
`extract`, que es orquestación sobre un gateway real; su comportamiento se verificó
corriéndolo contra la KB (ver el registro del plan). Acá está la lógica que decide.

EL TEST QUE MOTIVA ESTE ARCHIVO es `test_an_object_with_no_parts_is_not_a_success`:
probando con objetos inventados, `extract` reportó `extracted: 3, failed: 0` para tres
objetos que NO existen en la KB. Un objeto inexistente contaba como éxito, y el síntoma
recién aparecía en el `ingest`, lejos de la causa.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

pytestmark = pytest.mark.unit

# El script vive en scripts/ y no es un paquete: se carga por ruta.
_RUTA = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "bootstrap_kb.py"
_spec = importlib.util.spec_from_file_location("bootstrap_kb", _RUTA)
bk = importlib.util.module_from_spec(_spec)
sys.modules["bootstrap_kb"] = bk
_spec.loader.exec_module(bk)


INV = [
    {"name": "Company", "type": "Transaction", "parentPath": "Root/Finanzas"},
    {"name": "Cur", "type": "Transaction", "parentPath": "Root/Finanzas"},
    {"name": "PrcA", "type": "Procedure", "parentPath": "Root/Tools"},
    {"name": "PrcB", "type": "Procedure", "parentPath": "Root/Tools"},
    {"name": "WWFactura", "type": "WorkWithPlus", "parentPath": "Root/Web"},
]


# ─── el mini-DSL de selección ─────────────────────────────────────────────────


def test_all_selects_everything():
    for select in ("[all]", "*", None, "  "):
        elegidos, _ = bk.seleccionar(INV, select)
        assert len(elegidos) == len(INV), f"select={select!r}"


def test_type_selects_by_object_type():
    elegidos, informe = bk.seleccionar(INV, "type:Transaction")

    assert {o["name"] for o in elegidos} == {"Company", "Cur"}
    assert informe["terms"] == {"type:Transaction": 2}


def test_type_is_case_insensitive():
    elegidos, _ = bk.seleccionar(INV, "TYPE:transaction")

    assert len(elegidos) == 2


def test_a_bare_name_selects_that_object():
    elegidos, _ = bk.seleccionar(INV, "WWFactura")

    assert [o["name"] for o in elegidos] == ["WWFactura"]


def test_a_name_match_is_exact_not_a_prefix():
    """`Prc` no debe traer PrcA ni PrcB: la comparación de nombre es exacta.

    Un prefijo silencioso haría que un selector traiga más de lo pedido, y en un
    bootstrap de horas eso se descubre tarde.
    """
    elegidos, informe = bk.seleccionar(INV, "Prc")

    assert elegidos == []
    assert informe["unmatched"] == ["Prc"]


def test_a_slash_term_matches_the_parent_path():
    elegidos, _ = bk.seleccionar(INV, "Root/Finanzas")

    assert {o["name"] for o in elegidos} == {"Company", "Cur"}


def test_terms_are_ored():
    elegidos, informe = bk.seleccionar(INV, "type:Procedure;WWFactura")

    assert {o["name"] for o in elegidos} == {"PrcA", "PrcB", "WWFactura"}
    assert informe["terms"] == {"type:Procedure": 2, "WWFactura": 1}


def test_overlapping_terms_do_not_duplicate():
    """Dos términos que matchean el mismo objeto lo traen UNA vez.

    Sin el dedupe, ese objeto se extraería dos veces: el doble de tiempo de gateway
    para el mismo resultado.
    """
    elegidos, _ = bk.seleccionar(INV, "type:Transaction;Company;Root/Finanzas")

    assert len(elegidos) == 2


def test_the_report_breaks_down_every_term():
    """El desglose es lo que distingue "el corpus no lo tiene" de "escribí mal el tipo".

    Sin él, el llamador ve `selected: 0` y concluye sobre la KB en vez de sobre su
    propio argumento.
    """
    _, informe = bk.seleccionar(INV, "type:Transaction;type:Transacion")

    assert informe["terms"] == {"type:Transaction": 2, "type:Transacion": 0}
    assert informe["unmatched"] == ["type:Transacion"]


def test_empty_terms_are_ignored():
    elegidos, informe = bk.seleccionar(INV, "type:Transaction;;  ;")

    assert len(elegidos) == 2
    assert list(informe["terms"]) == ["type:Transaction"]


# ─── la ficha mecánica ────────────────────────────────────────────────────────


def _registro(**over) -> dict:
    base = {
        "name": "PrcMayorizar",
        "type": "Procedure",
        "parentPath": "Root/Contabilidad",
        "parts": {"Source": "For each Cliente\n  &x = 1\nEndFor"},
    }
    base.update(over)
    return base


def test_an_object_with_no_parts_is_not_a_success():
    """EL BUG QUE ESTE ARCHIVO EXISTE PARA PREVENIR.

    Un objeto que no existe en la KB responde sin partes. Si `_ficha_mecanica` devolviera
    una ficha, se indexaría un objeto vacío; si devuelve None, el llamador lo cuenta como
    `empty` — un estado con nombre propio, distinto de `extracted` y de `failed`.
    """
    assert bk._ficha_mecanica(_registro(parts={})) is None
    assert bk._ficha_mecanica(_registro(parts={"Source": "   "})) is None
    assert bk._ficha_mecanica({"name": "X", "type": "Procedure"}) is None


def test_a_ficha_carries_the_identity_the_check_requires():
    ficha = bk._ficha_mecanica(_registro())

    assert ficha["obj_name"] == "PrcMayorizar"
    assert ficha["obj_type"] == "Procedure"
    assert ficha["module"] == "Contabilidad"  # último segmento del parentPath


def test_sections_are_classified_by_part():
    """Structure/Variables/PatternInstance describen la forma; el resto, comportamiento."""
    ficha = bk._ficha_mecanica(
        _registro(parts={"Structure": "attr1, attr2", "Source": "For each"})
    )

    kinds = {s["kind"] for s in ficha["sections"]}
    assert kinds == {"structure", "logic"}


def test_an_oversized_part_is_truncated_and_says_so():
    """Una ficha que parece completa y no lo está hace que el agente responda con
    confianza sobre la mitad de un objeto."""
    largo = "x" * (bk.MAX_CHARS_POR_PARTE * 3)
    ficha = bk._ficha_mecanica(_registro(parts={"Source": largo}))

    texto = ficha["sections"][0]["text"]
    assert "TRUNCADO" in texto
    assert str(len(largo)) in texto, "tiene que decir el tamaño real"
    assert "genexus_read" in texto, "tiene que decir cómo obtener el resto"
    assert len(texto) < len(largo)


def test_a_part_below_the_limit_is_not_annotated():
    ficha = bk._ficha_mecanica(_registro(parts={"Source": "corto"}))

    assert "TRUNCADO" not in ficha["sections"][0]["text"]


def test_the_hash_is_over_the_kb_source_not_the_ficha():
    """kb_hash sobre el CRUDO: reingestar sin cambios en la KB tiene que dar `skipped`.

    Si se hasheara el texto de la ficha, cambiar el formato del encabezado o el tope de
    truncado haría que los miles de objetos salgan `reindexed` en la próxima corrida.
    """
    a = bk._ficha_mecanica(_registro())
    b = bk._ficha_mecanica(_registro())
    assert a["kb_hash"] == b["kb_hash"]

    distinto = bk._ficha_mecanica(_registro(parts={"Source": "OTRO source"}))
    assert distinto["kb_hash"] != a["kb_hash"]


def test_the_hash_ignores_the_order_of_the_parts():
    """json.dumps con sort_keys: el orden en que se leyeron las partes no puede
    filtrarse al hash y provocar reindexados fantasma."""
    uno = bk._ficha_mecanica(_registro(parts={"Source": "a", "Rules": "b"}))
    otro = bk._ficha_mecanica(_registro(parts={"Rules": "b", "Source": "a"}))

    assert uno["kb_hash"] == otro["kb_hash"]


def test_the_ficha_is_marked_as_mechanical():
    """Un hit mecánico es peor que uno redactado, y quien lo lea tiene que poder saberlo."""
    assert bk._ficha_mecanica(_registro())["_ficha_kind"] == "mechanical"


def test_a_mechanical_ficha_validates_against_the_real_validator():
    """La ficha derivada tiene que pasar el mismo validador que la redactada, o el
    bootstrap descubriría el problema recién al ingestar."""
    from src.ingestion.kb_fichas import validate_ficha

    ficha = bk._ficha_mecanica(_registro())
    ficha.pop("_ficha_kind")

    assert validate_ficha(ficha) == []


# ─── límites que no se tocan sin medir ────────────────────────────────────────


def test_the_batch_size_respects_the_gateway_timeout():
    """60 s de timeout / 2,2 s de mediana por read. 20 deja margen para Source grandes."""
    assert bk.BATCH_SIZE <= 25


def test_the_part_limit_keeps_fichas_small_enough_to_be_worth_embedding():
    """Medido: sin tope, 9 fichas dieron 67 chunks (7,4 c/u) y el núcleo serían ~13 h.
    Con 1.200 chars por parte: 3,0 chunks/ficha y ~5,3 h."""
    assert bk.MAX_CHARS_POR_PARTE <= 1500
