"""Tests de `scripts.scan_secrets`, el paso 1 de cada lote de docs/PLAN-INGESTA-SGR.md.

Lo que entra al RAG se lo devuelve a cualquier consulta, en cualquier proyecto. El
escáner es INFORMATIVO (los valores de ejemplo de los manuales se ingieren tal cual,
decisión del usuario): su trabajo es que una credencial real no pase sin que nadie
la vea, no decidir por la persona.
"""

import pytest

from scripts.scan_secrets import find_secrets

pytestmark = pytest.mark.unit


def _kinds(text: str) -> set[str]:
    return {kind for kind, _ in find_secrets(text)}


def test_a_password_assignment_is_reported():
    assert "password" in _kinds("Configurar el datasource con Password=Bt2024! en el web.xml")


def test_a_spanish_password_label_is_reported():
    assert "password" in _kinds("Usuario: btadmin  Contraseña: s3cr3t0")


def test_a_connection_string_is_reported():
    texto = "jdbc:sqlserver://10.20.1.15:1433;databaseName=BANTOTAL;user=sa"
    assert {"connection_string", "private_ip"} <= _kinds(texto)


def test_a_private_ip_is_reported_but_a_version_number_is_not():
    assert "private_ip" in _kinds("El servidor de aplicaciones está en 192.168.10.4")
    assert _kinds("Manual MDI-10201-GL-V3R1.22, versión 3.1.22.4") == set()


def test_an_internal_email_is_reported():
    assert "email" in _kinds("Consultas a soporte.core@bancocontactar.com")


def test_plain_prose_about_passwords_is_not_a_finding():
    # El manual EXPLICA el campo contraseña sin dar ningún valor: no hay nada que ver.
    assert _kinds("El usuario debe ingresar su contraseña en la pantalla de login.") == set()


def test_a_label_at_the_end_of_a_line_is_not_a_finding():
    # ETP-10605 (Giros Interagencias): "Clave:" es un rótulo de campo y el valor
    # NO está; lo que sigue es el párrafo de la línea siguiente. Cruzar el salto de
    # línea daba un falso positivo por cada rótulo.
    assert _kinds("Clave:\nCuando el giro se paga en otra agencia") == set()


def test_the_finding_carries_the_matched_text_for_the_human_to_judge():
    hallazgos = find_secrets("Password=Bt2024!")
    assert hallazgos and "Bt2024!" in hallazgos[0][1]
