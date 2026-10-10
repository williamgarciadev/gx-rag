"""Tests de `scripts.import_campus`: trae al árbol de docs los cursos del campus Bantotal.

El zip del campus llega con nombres en Unicode NFD (la tilde va aparte de la letra) y
con material que ya está en `bt_docs` por otra vía: manuales V2R3 cuya V3R1 entró del
portal SGR, videos procesados localmente, y el mismo video repetido en dos cursos.
Lo que se cuela de eso termina como versión mezclada o chunk duplicado en el top-k.
"""

import io
import unicodedata
import zipfile

import pytest

from scripts.import_campus import decide, member_rel, plan

pytestmark = pytest.mark.unit


def _md(tipo: str, estado: str, archivo: str = "", cuerpo: str = "texto") -> str:
    lineas = ["---", f'tipo: "{tipo}"', f'estado: "{estado}"']
    if archivo:
        lineas.append(f'archivo: "{archivo}"')
    return "\n".join(lineas + ["---", "", cuerpo])


def _zip(archivos: dict[str, str]) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for nombre, texto in archivos.items():
            z.writestr(f"bantotal-rag/corpus_md/{nombre}", texto)
    buf.seek(0)
    return zipfile.ZipFile(buf)


def test_a_complete_video_goes_in():
    entra, _ = decide("sistema_de_cuentas_vista/03_video_alta.md", {"tipo": "video", "estado": "completo"})
    assert entra


def test_an_incomplete_text_stays_out():
    entra, motivo = decide("x/01_pdf_niif.md", {"tipo": "pdf", "estado": "incompleto", "archivo": "a.pdf"})
    assert not entra and "parcial" in motivo


def test_the_course_index_goes_in_without_estado():
    entra, _ = decide("contactar/_curso.md", {"tipo": "curso"})
    assert entra


def test_a_v2r3_manual_already_in_bt_docs_as_v3r1_stays_out():
    fm = {"tipo": "pdf", "estado": "completo", "archivo": "MDI-10201-GL-V2R3.7 - Sistema de Préstamos (2).pdf"}
    entra, motivo = decide("contactar/23_pdf_mdi_sistema_de_préstamos.md", fm)
    assert not entra and "V3R1" in motivo


def test_a_manual_without_newer_version_goes_in():
    fm = {"tipo": "pdf", "estado": "completo", "archivo": "CAP-10111-GL-V2R2.1  - Conceptos de Contabilidad.pdf"}
    entra, _ = decide("sistema_de_contabilidad_ingreso_de_operaciones/02_pdf_conceptos.md", fm)
    assert entra


def test_the_table_inventory_stays_out_because_the_live_database_rules():
    fm = {"tipo": "pdf", "estado": "completo",
          "archivo": "MDU-99000-GL-V2R3.7 - Modelo de Datos Bantotal _ Inventario de Tablas.pdf"}
    entra, motivo = decide("contactar_capacitación_bantotal_v3/03_pdf_mdu_99000.md", fm)
    assert not entra and "bantotal-dev" in motivo


def test_a_video_already_processed_locally_stays_out():
    fm = {"tipo": "video", "estado": "completo"}
    entra, motivo = decide("buenas_prácticas_de_programación/02_video_exotic_quiero_globos_rojos.md", fm)
    assert not entra and "bt_docs" in motivo


def test_paths_come_out_in_nfc_even_if_the_zip_is_nfd():
    nfd = unicodedata.normalize("NFD", "sistema_de_préstamos/01_video_alta.md")
    items = plan(_zip({nfd: _md("video", "completo")}))
    assert items[0].rel == unicodedata.normalize("NFC", nfd)
    assert items[0].ingest


def test_a_name_without_the_utf8_flag_is_recovered_in_nfc():
    # El zip del campus no marca el flag 0x800: Python decodifica el nombre como cp437.
    nfd = unicodedata.normalize("NFD", "bantotal-rag/corpus_md/sistema_de_préstamos/01_video.md")
    info = zipfile.ZipInfo(nfd.encode("utf-8").decode("cp437"))
    info.flag_bits = 0
    assert member_rel(info) == "sistema_de_préstamos/01_video.md"


def test_the_same_video_with_a_different_course_breadcrumb_goes_in_once():
    items = plan(_zip({
        "contactar/37_video_cadena.md": _md("video", "completo", cuerpo="# Cadena\n\n> CONTACTAR › 9 abril\n\n[00:00] Hola"),
        "contactar_v3/38_video_cadena.md": _md("video", "completo", cuerpo="# Cadena\n\n> CONTACTAR V3 › Cierre\n\n[00:00] Hola"),
    }))
    assert sum(i.ingest for i in items) == 1


def test_the_same_content_in_two_courses_goes_in_once():
    items = plan(_zip({
        "contactar/37_video_cadena.md": _md("video", "completo", cuerpo="mismo"),
        "contactar_v3/38_video_cadena.md": _md("video", "completo", cuerpo="mismo"),
    }))
    entran = [i for i in items if i.ingest]
    assert len(entran) == 1
    repetido = next(i for i in items if not i.ingest)
    assert entran[0].rel in repetido.reason
