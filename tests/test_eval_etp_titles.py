"""Tests de `scripts.eval_etp_titles`: preguntas por tema y por código desde el título del ETP.

Cada ETP del portal SGR se titula "<tema> (<programas>)", p. ej. "Cierre Forzoso de
Caja (HMBC024)". De ahí salen dos preguntas sin escribir ninguna a mano: el tema,
como lo buscaría un funcional, y el código, como lo busca un desarrollador. Si el
parseo se equivoca, la prueba mide preguntas que nadie haría.
"""

import pytest

from scripts.eval_etp_titles import hit_rate, questions_from_title

pytestmark = pytest.mark.unit


def test_topic_and_single_program_code():
    assert questions_from_title("Cierre Forzoso de Caja (HMBC024)") == (
        "Cierre Forzoso de Caja", ["HMBC024"])


def test_several_codes_inside_the_parentheses():
    tema, codigos = questions_from_title("Fase Acumulativa Desacumulativa  (PRICO001-02)")
    assert tema == "Fase Acumulativa Desacumulativa"
    assert codigos == ["PRICO001"]
    _, codigos = questions_from_title("Generación Resultados en ME (PGEN508 - PFS99508)")
    assert codigos == ["PGEN508", "PFS99508"]


def test_a_title_without_program_code_gives_only_the_topic():
    assert questions_from_title("Mantenimiento de modos para FATCA") == (
        "Mantenimiento de modos para FATCA", [])


def test_words_in_parentheses_are_not_codes():
    # "(Novación de Garantía)" es parte del tema, no un programa.
    tema, codigos = questions_from_title(
        "Modificación Datos Adicionales (Novación de Garantía) (HPPG072 - PPPG072)")
    assert codigos == ["HPPG072", "PPPG072"]
    assert tema == "Modificación Datos Adicionales (Novación de Garantía)"


def test_hit_rate_counts_any_chapter_in_the_top_k():
    # rank = posición del MEJOR capítulo del ETP (None = no apareció).
    assert hit_rate([1, 3, 7, None], k=5) == 0.5
    assert hit_rate([1, 3, 7, None], k=10) == 0.75
