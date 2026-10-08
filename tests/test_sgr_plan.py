"""Tests de `scripts.sgr_plan`: qué documentos del portal SGR entran a un lote.

Cada regla nació de un caso real del 2026-10-07 (lote Core Colocaciones). Si una se
rompe, el lote baja normativa de otro país, duplica versiones o salta un manual nuevo
creyendo que ya estaba.
"""

import pytest

from scripts.sgr_plan import Doc, doc_key, latest_only, scope_of, split_lot, status_of

ROOT = "\\\\figari\\docsbantotal\\version2\\ORG.INTEGRAL\\WEBV3R1\\"


def _link(*parts):
    return ROOT + "\\".join(parts)


@pytest.mark.unit
def test_scope_reads_the_localization_segment_of_the_code():
    assert scope_of(_link("Manuales", "M", "MDI-10201-GL-V3R1.22 - Sistema de Préstamos.pdf")) == "GL"
    assert scope_of(_link("Otros", "ETP-10206-CO-V3R1.5 - Pólizas.pdf")) == "CO"
    assert scope_of(_link("Otros", "ETP-10801-UY-V2R2.1 - Lavado de dinero.pdf")) == "fuera"


@pytest.mark.unit
def test_scope_inside_a_country_folder_is_the_folder_not_the_code():
    # "MANUALES POR PAÍS\Argentina" holds -GL- manuals too: the folder decides.
    assert scope_of(_link("Manuales", "MANUALES POR PAÍS", "Colombia", "MDI-10110-GL-V3R1.7 - Contabilidad.pdf")) == "CO"
    assert scope_of(_link("Manuales", "MANUALES POR PAÍS", "Argentina", "MDI-10110-GL-V3R1.7 - Contabilidad.pdf")) == "fuera"


@pytest.mark.unit
def test_a_country_folder_at_any_depth_decides_even_against_a_gl_code():
    # Informes para el BCU uruguayo publicados con código -GL- bajo
    # "...Organismos Reguladores\Uruguay\..." y "REPORTES REGULATORIOS\Uruguay\...":
    # solo mirar las carpetas "por País" los dejaba pasar (2026-10-07, lote D1.4).
    link = _link("Manuales", "BANTOTAL  REPORTES REGULATORIOS", "Sistema de Información a Organismos Reguladores",
                 "Uruguay", "Informes Mensuales", "ETP-10801-GL-V2R3.1 - Operaciones Por Cuenta de Terceros.pdf")
    assert scope_of(link) == "fuera"
    colombia = _link("Otros Documentos", "Procesos Batch", "Procesos por País", "Colombia", "ETP-10201-CO-V3R1.1 - Saldos.pdf")
    assert scope_of(colombia) == "CO"


@pytest.mark.unit
def test_a_module_folder_under_a_country_folder_is_not_a_country():
    # "MANUALES POR PAÍS\BANTOTAL CORE  NÚCLEO" holds 128 Global manuals (2026-10-07),
    # Contrapartes V3R1.29 among them: reading the child as a country dropped them all.
    link = _link("Manuales", "MANUALES POR PAÍS", "BANTOTAL CORE  NÚCLEO", "Sistema de Contrapartes",
                 "MDI-10109-GL-V3R1.29 - Sistema de Contrapartes.pdf")
    assert scope_of(link) == "GL"


@pytest.mark.unit
def test_scope_without_localization_or_code_is_left_to_the_user():
    assert scope_of(_link("Otros", "ECP-000302-V3R1.1 - Renovación Pólizas.pdf")) == "sin-país"
    assert scope_of(_link("Manuales", "M", "Cálculo de intereses Bantotal.pdf")) == "sin-país"


@pytest.mark.unit
def test_doc_key_is_code_plus_title_and_tolerates_spaces_in_the_code():
    # "MDI-10211- GL-V3R1.1" is how the portal really names it.
    (key, version) = doc_key("MDI-10211- GL-V3R1.1 - Originación de Créditos l Flujo Consumo.pdf")
    assert key == ("MDI-10211", "originacióndecréditoslflujoconsumo")
    assert version == (3, 1, 1)


@pytest.mark.unit
def test_a_copy_marker_at_the_end_does_not_make_a_new_document():
    # "... (PRIC003) (2).pdf" es una copia idéntica (mismo hash) del original: se borró en el
    # lote 3 y el lote D2.1 la volvió a bajar porque el "(2)" cambiaba el título (2026-10-07).
    original = doc_key("ETP-10114-CO-V3R1.1 - Cálculo Segmentos Carteras SARC (PRIC003).pdf")
    copia = doc_key("ETP-10114-CO-V3R1.1 - Cálculo Segmentos Carteras SARC (PRIC003) (2).pdf")
    assert copia == original
    # Un paréntesis que es parte del nombre (programas) NO es un marcador de copia.
    assert doc_key("ETP-10114-CO-V3R1.1 - Fase (PRICO001-02).pdf")[0][1].endswith("prico00102")


@pytest.mark.unit
def test_between_a_copy_and_its_original_the_original_wins():
    # El inventario a veces lista la copia "(2)" antes que el original: quedarse con la primera
    # bajaba la copia. A igual versión, gana el nombre sin marcador.
    docs = [Doc(link="copia", name="ETP-10201-GL-V3R1.1 - Mora en Préstamos (PRTE041) (2).pdf"),
            Doc(link="original", name="ETP-10201-GL-V3R1.1 - Mora en Préstamos (PRTE041).pdf")]
    kept, _ = latest_only(docs)
    assert [d.link for d in kept] == ["original"]


@pytest.mark.unit
def test_one_code_shared_by_different_documents_gives_different_keys():
    # MDI-10201 names Préstamos, Seguros, Modelador and Vínculo: four documents.
    prestamos, _ = doc_key("MDI-10201-GL-V3R1.22 - Sistema de Préstamos.pdf")
    seguros, _ = doc_key("MDI-10201-GL-V3R1.5 - Sistema de Seguros.pdf")
    assert prestamos != seguros


@pytest.mark.unit
def test_latest_only_keeps_the_highest_version_of_each_document():
    docs = [
        Doc(link="a", name="MDU-10202-GL-V3R1.3 - Sistema de Descuentos.pdf"),
        Doc(link="b", name="MDU-10202-GL-V3R1.4 - Sistema de Descuentos.pdf"),
        Doc(link="c", name="MDU-10202-GL-V3R1.5 - Modelador de Productos de Crédito Descuentos.pdf"),
    ]
    kept, dropped = latest_only(docs)
    assert {d.link for d in kept} == {"b", "c"}
    assert [d.link for d in dropped] == ["a"]


@pytest.mark.unit
def test_latest_only_compares_versions_numerically_not_as_text():
    # As text "V3R1.9" > "V3R1.22"; it is the older one.
    docs = [
        Doc(link="old", name="MDI-10201-GL-V3R1.9 - Sistema de Préstamos.pdf"),
        Doc(link="new", name="MDI-10201-GL-V3R1.22 - Sistema de Préstamos.pdf"),
    ]
    kept, _ = latest_only(docs)
    assert [d.link for d in kept] == ["new"]


@pytest.mark.unit
def test_a_document_in_two_folders_lands_in_exactly_one_lot():
    # MDI-10103 "Manual de Definición de RTE" está en Núcleo Y en RTE's Genéricas. Planear
    # carpeta por carpeta lo bajaba en los dos lotes (D1.1 y D2.6, 2026-10-07).
    name = "MDI-10103-GL-V3R1.1 - Manual de Definición de RTE.pdf"
    docs = [Doc(link=_link("Manuales", "BANTOTAL CORE  NÚCLEO", "X", name), name=name),
            Doc(link=_link("Otros Documentos", "RTE's Genéricas", name), name=name)]
    kept, _ = latest_only(docs)
    lots = [split_lot(kept, prefix) for prefix in ("Manuales/BANTOTAL CORE  NÚCLEO/", "Otros Documentos/RTE's Genéricas/")]
    assert sum(len(lot) for lot in lots) == 1


@pytest.mark.unit
def test_status_against_what_the_rag_already_has():
    have = {doc_key("MDI-10201-GL-V3R1.20 - Sistema de Préstamos.pdf")[0]: {(3, 1, 20)}}
    assert status_of("MDI-10201-GL-V3R1.20 - Sistema de Préstamos.pdf", have) == "ya está"
    assert status_of("MDI-10201-GL-V3R1.22 - Sistema de Préstamos.pdf", have) == "versión nueva"
    assert status_of("MDI-10201-GL-V3R1.5 - Sistema de Préstamos.pdf", have) == "versión vieja"
    assert status_of("MDI-10201-GL-V3R1.6 - Modelador de Productos.pdf", have) == "nuevo"
    assert status_of("Cálculo de intereses.pdf", have) == "sin código"
