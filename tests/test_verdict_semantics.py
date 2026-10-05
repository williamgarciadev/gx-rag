"""Verdict semantics before the catalog gets populated.

Populating first would be the wrong order. The catalog is fed from GX18 sources
and `api.tsv` carries no version column, so every row lands with
`version_since = NULL` — and the current code treats a missing version as "do
not gate" and answers VÁLIDO. Asked from a Bantotal GX16 KB that is a FALSE
VALID, the failure mode the whole layer exists to prevent: nothing surfaces it,
the code simply does not compile later (`Abs()` → src0294).

So the repository needs a fourth verdict first: the catalog can prove a name
exists in the version it was built from, and cannot prove anything about an
older one. That is a PISTA — the word the project's own rule uses.
"""

import pytest

from src.syntax_db.gx_repository import verify_syntax

pytestmark = pytest.mark.unit


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    from src.syntax_db import models

    monkeypatch.setattr(models, "_engine", None)
    models.get_engine(tmp_path / "test.db")
    yield
    monkeypatch.setattr(models, "_engine", None)


def _add(name, object_type="function", version_since=None, source="wiki"):
    from src.syntax_db.models import GxSyntax, get_session

    session = get_session()
    session.add(
        GxSyntax(
            name=name,
            object_type=object_type,
            version_since=version_since,
            source=source,
            signature={"raw": f"{name}()"},
        )
    )
    session.commit()
    session.close()


# ─── The false-valid this exists to stop ──────────────────────────────────────


def test_a_versionless_row_asked_about_an_older_version_is_a_hint_not_valid(temp_db):
    # The Abs() case: in the GX18-sourced catalog, absent from GX16.
    _add("Abs")

    result = verify_syntax("Abs(&x)", version="gx16")

    assert result["verdict"] == "PISTA"


def test_the_hint_says_how_to_confirm(temp_db):
    # A verdict the agent cannot act on is just a softer false valid.
    _add("Abs")

    result = verify_syntax("Abs(&x)", version="gx16")

    assert "gx18" in result["reason"].lower()
    assert "gx16" in result["reason"].lower()


def test_a_versionless_row_asked_about_the_catalog_version_is_valid(temp_db):
    # The catalog is built from GX18 sources, so GX18 is exactly what it proves.
    _add("Abs")

    assert verify_syntax("Abs(&x)", version="gx18")["verdict"] == "VÁLIDO"


def test_a_versionless_row_asked_about_a_newer_version_is_valid(temp_db):
    _add("Abs")

    assert verify_syntax("Abs(&x)", version="gx19")["verdict"] == "VÁLIDO"


def test_without_a_version_question_the_answer_stays_valid(temp_db):
    # No version asked, no version claim made. Unchanged behaviour.
    _add("Trim")

    assert verify_syntax("Trim(&x)")["verdict"] == "VÁLIDO"


def test_an_explicit_version_since_still_gates_as_before(temp_db):
    # Known data beats the catalog-wide default: this row says GX17 onwards.
    _add("Nuevo", version_since="gx17")

    assert verify_syntax("Nuevo()", version="gx16")["verdict"] == "INVÁLIDO"
    assert verify_syntax("Nuevo()", version="gx17")["verdict"] == "VÁLIDO"
    assert verify_syntax("Nuevo()", version="gx18")["verdict"] == "VÁLIDO"


# ─── C-02: the fuzzy fallback matching everything ─────────────────────────────


def test_an_empty_snippet_is_not_verifiable(temp_db):
    # token = "" made the fallback LIKE '%%', which matches every row: the
    # catalog answered INVÁLIDO with five arbitrary suggestions.
    _add("Trim")
    _add("Format")

    result = verify_syntax("")

    assert result["verdict"] == "NO_VERIFICABLE"
    assert "sugerencias" not in result


def test_a_one_character_token_is_not_fuzzy_matched(temp_db):
    # LIKE '%a%' hits most of the catalog; suggestions from it are noise.
    _add("Format")

    result = verify_syntax("a")

    assert result["verdict"] == "NO_VERIFICABLE"


def test_a_real_near_miss_still_gets_suggestions(temp_db):
    # The fallback has to keep working for what it was for.
    _add("Format")

    result = verify_syntax("Formatt(&x)")

    assert result["verdict"] == "INVÁLIDO"
    assert "Format" in result["sugerencias"]


# ─── C-01: list_methods contradicting the 'since' semantics ───────────────────


def test_list_methods_uses_since_semantics_not_string_equality(temp_db):
    # version_since means "exists FROM this version onwards", so asking for
    # GX18 must include a method introduced in GX17. Exact string equality
    # answered an empty list — the same contract, two opposite meanings, in one
    # module.
    from src.syntax_db.gx_repository import list_methods

    _add("Viejo", object_type="Transaction", version_since="gx16")
    _add("Nuevo", object_type="Transaction", version_since="gx17")

    assert sorted(list_methods("Transaction", version="gx18")) == ["Nuevo", "Viejo"]


def test_list_methods_excludes_what_did_not_exist_yet(temp_db):
    from src.syntax_db.gx_repository import list_methods

    _add("Viejo", object_type="Transaction", version_since="gx16")
    _add("Nuevo", object_type="Transaction", version_since="gx18")

    assert list_methods("Transaction", version="gx16") == ["Viejo"]


def test_list_methods_keeps_versionless_rows(temp_db):
    # Most rows have no version_since; dropping them would empty the catalog.
    from src.syntax_db.gx_repository import list_methods

    _add("SinVersion", object_type="Transaction")

    assert list_methods("Transaction", version="gx18") == ["SinVersion"]
