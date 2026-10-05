"""Limpieza del texto que el OCR saca de los frames de un video.

Dos problemas medidos en el piloto «Curso Contabilidad — Clientes y contabilidad»:

- La interfaz (menús, cinta de Excel, barra del navegador) se repite en casi todos
  los frames y ensucia la búsqueda léxica. Se detecta por frecuencia, pero el OCR
  lee la misma línea con variaciones («Marcelo k» / «Marcelo»), así que se compara
  por una CLAVE normalizada y no por el texto exacto.
- El OCR separa la etiqueta de un documento de su número («Cedula», «17258608»).
  Un filtro línea por línea no ve la relación; hay que mirar la línea anterior.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

# Etiquetas tras las cuales el número que sigue es un documento de identidad.
_ID_LABEL = re.compile(r"^(c[ée]dula|nit|documento|c\.\s?c\.?)$", re.IGNORECASE)
_ID_NUMBER = re.compile(r"^\d{6,11}$")
ID_MASK = "[DOC]"

_MIN_USEFUL_LINES = 5
# 1 a 4 palabras con mayúscula inicial y solo letras: «Juan Pérez».
_LOOKS_LIKE_NAME = re.compile(r"^[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+( [A-ZÁÉÍÓÚÑ][a-záéíóúñü]+){0,3}$")


def _line_key(line: str) -> str:
    """Clave para comparar líneas de OCR: sin tildes, en minúscula y sin tokens de
    un carácter, que es justo donde el OCR varía entre frames («Marcelo k»).
    Una clave vacía es ruido: íconos leídos como un glifo suelto («H», «三»)."""
    ascii_text = unicodedata.normalize("NFKD", line).encode("ascii", "ignore").decode()
    tokens = re.findall(r"[a-z0-9]+", ascii_text.lower())
    return " ".join(t for t in tokens if len(t) > 1)


def interface_keys(frames: list[list[str]], min_frames: int = 5) -> set[str]:
    """Claves de las líneas presentes en al menos `min_frames` frames.

    El umbral es ABSOLUTO y no una fracción del video: la cinta de Excel del piloto
    salió en 11 de 83 frames (un tramo), y con el 20 % no se detectaba. Una línea
    marcada como interfaz igual se muestra la primera vez, así que un falso
    positivo cuesta poco; el título de la pantalla lo protege `screen_title`.
    """
    counts = Counter(key for lines in frames for key in {_line_key(l) for l in lines})
    return {key for key, n in counts.items() if key and n >= min_frames}


def is_useful_screen(lines: list[str]) -> bool:
    """Decide si lo que se ve en un frame merece abrir una sección.

    Medido en el piloto: una pantalla Bantotal trae ~40 líneas y el Excel del
    instructor ~60; el recuadro de Teams con la cámara apagada, una (el nombre).
    Fuera también la galería de Teams: varias líneas, pero todas nombres.
    """
    if len(lines) < _MIN_USEFUL_LINES:
        return False
    return not all(_LOOKS_LIKE_NAME.match(l.strip()) for l in lines)


def screen_title(lines: list[str]) -> str:
    """Nombre de la pantalla que muestra el frame.

    Las pantallas Descartes muestran el título en la pestaña y en el encabezado, y
    el OCR lo lee dos veces, aunque la línea sea interfaz (una pantalla que vuelve
    en varias secciones lo es). Medido: 58 de 67 frames Bantotal del piloto.
    Sin repetición devuelve "": adivinar con "la primera línea que no es interfaz"
    dio basura en 6 de 22 secciones de otro video («Imprimirt», «1,000.00», «F12»).
    """
    seen: set[str] = set()
    for line in lines:
        key = _line_key(line)
        # Dos palabras con letras: un importe o una fecha también se repiten
        # («1,000.00», «28/04/14») y no nombran ninguna pantalla.
        if sum(t.isalpha() for t in key.split()) >= 2 and key in seen:
            return line.strip()
        seen.add(key)
    return ""


def document_numbers(frames: list[list[str]]) -> set[str]:
    """Números que en ALGÚN frame aparecen justo después de una etiqueta de documento.

    Se recolectan en una pasada previa porque el mismo número sale en otros frames
    sin la etiqueta: suelto, o pegado a un nombre («17258608 Carolina»).
    """
    found: set[str] = set()
    for lines in frames:
        texts = [l.strip() for l in lines]
        for label, value in zip(texts, texts[1:]):
            if _ID_LABEL.match(label) and _ID_NUMBER.match(value):
                found.add(value)
    return found


def mask_document_numbers(text: str, ids: set[str]) -> str:
    for number in ids:
        text = re.sub(rf"(?<!\d){number}(?!\d)", ID_MASK, text)
    return text


def clean_screen_lines(
    lines: list[str], interface: set[str], shown: set[str], ids: set[str] = frozenset()
) -> list[str]:
    """Limpia las líneas de un frame.

    Descarta el ruido, enmascara los documentos (los ya identificados en `ids` y el
    número que sigue a una etiqueta) y deja pasar la interfaz solo la primera vez
    (`shown` se comparte entre frames).
    """
    cleaned: list[str] = []
    after_id_label = False
    for line in lines:
        text = mask_document_numbers(line.strip(), ids)
        key = _line_key(text)
        if not key:
            continue
        if after_id_label and _ID_NUMBER.match(text):
            cleaned.append(ID_MASK)
            after_id_label = False
            continue
        after_id_label = bool(_ID_LABEL.match(text))
        if key in interface:
            if key in shown:
                continue
            shown.add(key)
        cleaned.append(text)
    return cleaned
