"""Lectura de un snippet GeneXus: qué nombres hay que consultar en el catálogo.

parse_snippet es puro: no toca la base. Por eso estos tests no usan temp_db, y
cada bug de lectura se fija acá sin pasar por el juicio contra el catálogo.
El contrato que importa: ningún nombre que el agente NO escribió puede salir de
acá, porque verify_syntax lo trataría como un typo y diría INVÁLIDO
(docs/AUDITORIA-2026-10-17.md, H1-H3).
"""

import pytest

from src.syntax_db.snippet_parser import parse_snippet

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "snippet, nombre",
    [
        ("Trim(&y)", "Trim"),
        ("&x = Trim(&y)", "Trim"),
        ("If Len(&s) > 0", "Len"),
        ("If(Len(&s) > 0)", "Len"),
        ("Do while Len(&s) > 0", "Len"),
    ],
)
def test_la_llamada_se_encuentra_en_cualquier_posicion(snippet, nombre):
    """H1: antes solo se reconocía la función si abría el snippet."""
    assert parse_snippet(snippet).names[0] == nombre


def test_las_palabras_de_control_nunca_son_nombres():
    p = parse_snippet("If(Len(&s) > 0)")
    assert "If" not in p.names


def test_un_comando_de_control_sin_llamadas_no_inventa_nombres():
    """'For Each Customer' daba el token 'Each Customer' y un INVÁLIDO fabricado."""
    assert parse_snippet("For Each Customer").names == ()


@pytest.mark.parametrize(
    "snippet",
    ["Msg('Error. Revise los datos')", 'Msg("Error. Revise")', "Msg('a.b') // c.d()"],
)
def test_un_punto_dentro_de_un_literal_o_comentario_no_es_un_miembro(snippet):
    """H2: 'Error. Revise' se leía como receptor 'Error', miembro 'Revise'."""
    p = parse_snippet(snippet)
    assert p.receiver is None
    assert p.names == ("Msg",)


@pytest.mark.parametrize(
    "snippet, receptor, miembro",
    [
        ("&t.Length()", "&t", "Length"),
        ("Date.AddDays(1)", "Date", "AddDays"),
        ("PJCCY657.Call(&a)", "PJCCY657", "Call"),
        ("&x = &y.Trim()", "&y", "Trim"),
        ("&bc.Name", "&bc", "Name"),
    ],
)
def test_miembro_sobre_receptor(snippet, receptor, miembro):
    p = parse_snippet(snippet)
    assert p.receiver == receptor
    assert p.names[0] == miembro


def test_un_numero_decimal_no_es_un_receptor():
    p = parse_snippet("&x = Round(&y, 1.5)")
    assert p.receiver is None
    assert p.names == ("Round",)


def test_miembro_anidado_en_una_llamada_va_primero_y_la_llamada_despues():
    """El miembro es lo más específico; la función que lo envuelve también se consulta."""
    p = parse_snippet("&x = Trim(&y.ToString())")
    assert p.names == ("ToString", "Trim")


@pytest.mark.parametrize(
    "snippet, nombre",
    [("Len", "Len"), ("AddYears method", "AddYears method"), ("Default", "Default")],
)
def test_identificador_suelto_o_titulo_de_wiki(snippet, nombre):
    """Sin llamada ni miembro, el snippet entero es el nombre: así se pregunta
    por un título de la wiki ('AddYears method')."""
    assert parse_snippet(snippet).names == (nombre,)


@pytest.mark.parametrize("snippet", ["", "   ", "do", "if else", "return true", "''"])
def test_sin_tokens_no_hay_nombres(snippet):
    assert parse_snippet(snippet).names == ()
