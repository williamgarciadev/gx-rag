"""Importación de gxtypes.xml al catálogo gx_syntax.

`--version` es la versión del GeneXus INSTALADO del que se leyó el XML, no la
versión en que apareció cada método: el XML no trae ese dato. El 2026-10-05 se
midió el daño de confundirlas: las 706 filas quedaron con
`version_since = 18.0.15.188745`, y `verify_syntax("&d.AddDays(1)", version="gx16")`
respondía INVÁLIDO («existe a partir de 18.0.15, no está disponible en gx16»).
AddDays sí existe en GX16, y gx16 es la versión de todas las KBs Bantotal.
"""

import pytest

pytestmark = pytest.mark.unit

INSTALL_VERSION = "18.0.15.188745"


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    from src.syntax_db import models

    monkeypatch.setattr(models, "_engine", None)
    db = tmp_path / "test.db"
    models.get_engine(db)
    yield db
    monkeypatch.setattr(models, "_engine", None)


def _import_add_days(db):
    from scripts.import_gxtypes import DEFAULT_DOC_ID, MethodEntry, import_entries

    entry = MethodEntry(class_name="DateTime", method_name="AddDays",
                        returns="DateTime", params=(("Days", "Numeric"),))
    import_entries([entry], INSTALL_VERSION, DEFAULT_DOC_ID, db)


def test_install_version_is_not_stored_as_availability(temp_db):
    from src.syntax_db.models import GxSyntax, get_session

    _import_add_days(temp_db)

    session = get_session()
    row = session.query(GxSyntax).filter(GxSyntax.name == "AddDays").one()
    session.close()
    assert row.version_since is None
    # La versión del install no se pierde: queda en la procedencia.
    assert INSTALL_VERSION in row.source_doc_id


def test_a_gxtypes_method_asked_for_gx16_is_a_hint_not_invalid(temp_db):
    from src.syntax_db.gx_repository import verify_syntax

    _import_add_days(temp_db)

    assert verify_syntax("&d.AddDays(1)", version="gx16")["verdict"] == "PISTA"
    assert verify_syntax("&d.AddDays(1)", version="gx18")["verdict"] == "VÁLIDO"
