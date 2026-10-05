"""Limpieza del texto que el OCR saca de cada frame de un video.

Casos tomados del piloto «Curso Contabilidad — Clientes y contabilidad»
(06/04/21): ahí se midieron los dos defectos que estos tests fijan.
"""

import pytest

from src.ingestion.screen_text import (
    clean_screen_lines,
    document_numbers,
    interface_keys,
    is_useful_screen,
    screen_title,
)

pytestmark = pytest.mark.unit


def test_interface_line_is_detected_across_ocr_variants():
    # El OCR lee la misma cinta de Excel con variaciones de un carácter:
    # «Marcelo k» en unos frames, «Marcelo» en otros. Comparando texto exacto
    # ninguna de las dos llega al umbral y la cinta se repite en cada sección.
    frames = [["Autoguardado", "Marcelo k", "Cuenta 999"],
              ["Autoguardado", "Marcelo", "Cuenta 739"],
              ["Autoguardado", "Marcelo k", "Pizarra de Tasas"],
              ["Autoguardado", "Marcelo", "Listas Negras"]]

    keys = interface_keys(frames, min_frames=3)

    shown: set[str] = set()
    first = clean_screen_lines(frames[0], keys, shown)
    second = clean_screen_lines(frames[1], keys, shown)
    assert "Autoguardado" in first
    assert not any(l.startswith("Marcelo") for l in second)


def test_interface_threshold_is_absolute_not_a_fraction_of_the_video():
    # La cinta de Excel salió en 11 de 83 frames: interfaz de un TRAMO del video.
    # Con un umbral relativo (20 % = 16,6 frames) no se detectaba. Con uno absoluto
    # sí, sin importar cuánto dure el resto del video.
    excel = [["Autoguardado", f"celda {i}"] for i in range(5)]
    bantotal = [["Menú de Clientes", f"pantalla {i}"] for i in range(78)]

    assert "autoguardado" in interface_keys(excel + bantotal, min_frames=5)


def test_screen_title_is_the_line_the_screen_shows_twice():
    # Las pantallas Descartes muestran el título en la pestaña y en el encabezado,
    # y el OCR lo lee dos veces. Medido: 58 de 67 frames Bantotal del piloto.
    lines = ["DLYA : : Bantotal - Trabajo", "Contabilidad",
             "Mantenimiento de Listas Negras", "Mantenimiento de Listas Negras", "Filtrar por"]
    interface = {"dlya bantotal trabajo", "contabilidad"}

    assert screen_title(lines) == "Mantenimiento de Listas Negras"


def test_screen_title_survives_when_the_screen_repeats_across_the_video():
    # Con umbral bajo, el título de una pantalla que se repite en 6 secciones queda
    # como interfaz. Si el título se eligiera entre las líneas que NO son interfaz,
    # esas secciones perderían el nombre de su pantalla.
    lines = ["Menú", "Mantenimiento del Plan de Códigos Contables",
             "Mantenimiento del Plan de Códigos Contables", "Desde C. Contable"]
    interface = {"menu", "mantenimiento del plan de codigos contables"}

    assert screen_title(lines) == "Mantenimiento del Plan de Códigos Contables"


@pytest.mark.parametrize("value", ["1,000.00", "28/04/14"])
def test_a_repeated_amount_or_date_is_not_a_title(value):
    # El importe y la fecha de una operación salen dos veces en la pantalla de
    # ingreso: se repiten igual que un título, pero no nombran nada (medido en
    # el video de Definición de transacciones).
    lines = ["Importe", value, "Moneda", value, "Confirmar"]

    assert screen_title(lines) == ""


def test_header_that_extends_the_tab_title_is_the_title():
    # Líneas reales del frame de las 03:01 del video de Definición de transacciones:
    # la pestaña dice el nombre corto y el encabezado le agrega la operación. Con
    # la comparación exacta no había título y el paso heredaba uno falso
    # («Autorización de Excepciones»).
    lines = ["Inicio Accesos :: Menú de Instalación de Bantotal", "Bantotal",
             "Ingreso de Operaciones", "Ingreso de Operaciones - Depósito en CV",
             "CONFIRMACION", "Concepto", "Cuenta Vista", "1,000.00", "1,000.00"]

    assert screen_title(lines) == "Ingreso de Operaciones - Depósito en CV"


def test_tab_title_truncated_by_the_ocr_still_matches_the_header():
    # Frame real de las 03:05: la pestaña sale cortada en la segunda lectura.
    lines = ["Inicio Accesos :: Menú de Instala", "Windows Internet Explorer", "Bantotal",
             "Ingreso de Operaciones", "Ingreso de Opera"]

    assert screen_title(lines) == "Ingreso de Operaciones"


def test_a_one_word_line_does_not_prefix_match_a_longer_one():
    # «Cuenta» y «Cuenta Vista» son dos etiquetas distintas de la misma grilla.
    lines = ["Cuenta", "Cuenta Vista", "Moneda", "Moneda Extranjera"]

    assert screen_title(lines) == ""


def test_without_a_repeated_title_there_is_no_title():
    # El respaldo "primera línea que no es interfaz" dio basura en 6 de 22
    # secciones del video de Definición de transacciones: «Imprimirt», «errar»,
    # «1,000.00», «F12». Sin repetición no se adivina; el paso hereda el título
    # anterior (VideoSynthesizer).
    lines = ["Imprimirt", "errar", "1,000.00", "Confirmación"]

    assert screen_title(lines) == ""


@pytest.mark.parametrize("lines, useful", [
    (["Juan Pérez"], False),                                        # Teams, cámara apagada
    (["Ana Gómez", "Luis Mora", "Juan Pérez", "Eva Ruiz", "Iván Díaz"], False),  # galería de Teams
    (["DLYA : : Bantotal", "Inicio", "Clientes", "Mantenimiento de Personas", "Filtrar por"], True),
])
def test_useful_screen_needs_five_lines_that_are_not_all_names(lines, useful):
    # Criterio medido en el piloto: una pantalla Bantotal trae ~40 líneas; el
    # recuadro de Teams, una (el nombre de quien habla).
    assert is_useful_screen(lines) is useful


def test_number_after_an_id_label_is_masked():
    # En el Excel del ejemplo el OCR devuelve la etiqueta y el número en líneas
    # separadas: «Cedula», «17258608». Ese número es un documento de identidad.
    lines = ["Colombia", "Cedula", "17258608", "NIT", "22223333", "Cuenta 999"]

    cleaned = clean_screen_lines(lines, set(), set())

    assert "17258608" not in cleaned
    assert "22223333" not in cleaned
    assert cleaned.count("[DOC]") == 2


def test_a_document_number_is_masked_in_every_frame_once_identified():
    # Medido en el piloto: «17258608» sale 27 veces, pero solo en algunos frames
    # con la etiqueta justo antes. En el resto viene suelto o pegado a un nombre
    # («17258608 Carolina»). Una vez visto como documento, es documento en todo
    # el video.
    frames = [["Cedula", "17258608", "Carolina"],
              ["Cuenta 999", "17258608 Carolina", "Integra la cuenta"]]

    ids = document_numbers(frames)
    cleaned = clean_screen_lines(frames[1], set(), set(), ids)

    assert not any("17258608" in l for l in cleaned)
    assert "[DOC] Carolina" in cleaned


def test_accounting_codes_are_not_masked():
    # Los códigos contables también son números largos, pero son el contenido
    # didáctico: solo se enmascara lo que sigue a una etiqueta de documento.
    lines = ["C. Contable", "167302001", "Prést.Amort. Comercial"]

    assert clean_screen_lines(lines, set(), set()) == lines


def test_single_glyph_noise_lines_are_dropped():
    # Íconos de la cinta que el OCR lee como un carácter suelto: «H», «X», «三».
    lines = ["H", "X", "三", "Insertar", "a"]

    assert clean_screen_lines(lines, set(), set()) == ["Insertar"]
