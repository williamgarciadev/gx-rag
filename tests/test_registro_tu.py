"""El texto del repo va en "tú", nunca en voseo.

No es estética: las descripciones de las tools y los mensajes de respuesta son
el PROMPT del agente consumidor (CLAUDE.md), y un agente imita el registro del
texto que recibe. Un "verificá" en `_VERDICT_GUIDANCE` termina en la respuesta
que el agente le da a un usuario colombiano.

Dos detectores, porque el voseo tiene dos formas:
- Agudas (usá, pasás, corré, podés): patrón genérico con lista de excepciones.
  Si una palabra legítima cae aquí, agrégala a _AGUDAS_LEGITIMAS.
- Enclíticos sin tilde (Confirmalo, verificala): un patrón genérico pescaría
  "modelo" o "fila", así que van por lista. En "tú" llevan tilde (Confírmalo).
"""

import re

import pytest

from tests._repo import RAIZ as _RAIZ
from tests._repo import archivos_py

pytestmark = pytest.mark.unit

# ií: la clase de regex [IÍ] de bulk_loader, no una palabra.
_AGUDAS_LEGITIMAS = frozenset(
    """
    está esté estás estés será sé sí qué acá allá ahí aquí así allí quizá caché
    más jamás además demás atrás después través revés inglés francés país
    interés cuáles panamá podrá hará usará tendrá deberá quedará dará verá irá
    habrá devolverá
    ií
    """.split()
)

_AGUDA = re.compile(r"\b[a-záéíóúñ]+(?:á|é|í|ás|és|ís)\b", re.IGNORECASE)

_ENCLITICOS_VOSEO = re.compile(
    r"\b(?:confirmalo|confirmala|verificalo|verificala|tratalo|usalo|usala|"
    r"subilo|omitilo|leelo|reingestala|borralas|tomalo|afirmalo)\b",
    re.IGNORECASE,
)


def test_el_codigo_no_tiene_voseo():
    hallazgos = []
    for archivo in archivos_py("src", "scripts"):
        for n, linea in enumerate(archivo.read_text(encoding="utf-8").splitlines(), 1):
            voseo = [
                w for w in _AGUDA.findall(linea) if w.lower() not in _AGUDAS_LEGITIMAS
            ] + _ENCLITICOS_VOSEO.findall(linea)
            if voseo:
                hallazgos.append(f"{archivo.relative_to(_RAIZ)}:{n}: {', '.join(voseo)}")

    assert not hallazgos, "Voseo (pásalo a 'tú'):\n" + "\n".join(hallazgos)
