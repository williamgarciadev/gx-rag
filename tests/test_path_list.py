"""Tests de `scripts._path_list`: las listas de lote de docs/PLAN-INGESTA-SGR.md.

Las rutas de un lote viajan en un archivo UTF-8 y no por la consola: impresas en
cp1252, las tildes de "NÚCLEO" llegaban rotas y los scripts recibían rutas que "no
existen" (2026-10-07). Esta lectura la comparten scan_secrets, ingest_lote y
verify_ingest; si una la leyera distinto, cada paso del lote trabajaría sobre otro
conjunto de archivos.
"""

from pathlib import Path

import pytest

from scripts._path_list import read_path_list

pytestmark = pytest.mark.unit


def test_reads_one_path_per_line_keeping_accents(tmp_path):
    lista = tmp_path / "lote.txt"
    lista.write_text("F:/docs/BANTOTAL CORE - NÚCLEO/a.pdf\nF:/docs/b.pdf\n", encoding="utf-8")
    assert read_path_list(lista) == [Path("F:/docs/BANTOTAL CORE - NÚCLEO/a.pdf"), Path("F:/docs/b.pdf")]


def test_skips_blank_lines_and_surrounding_spaces(tmp_path):
    lista = tmp_path / "lote.txt"
    lista.write_text("\n  F:/docs/a.pdf  \n\n", encoding="utf-8")
    assert read_path_list(lista) == [Path("F:/docs/a.pdf")]
