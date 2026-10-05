"""Workstream C — reviving the binary-verdict syntax catalog (the 'cut' layer).

The syntax DB engine is a module-level singleton; seeding it with a temp SQLite
path makes every get_session() in every module hit the throwaway DB, so these
tests never touch data/syntax.db.
"""

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    from src.syntax_db import models

    monkeypatch.setattr(models, "_engine", None)
    models.get_engine(tmp_path / "test.db")  # seed the singleton to the temp DB
    yield
    monkeypatch.setattr(models, "_engine", None)


# ─────────────────────────────────────────────────────────────────────────────
# Candidate repository — staging + human-gated promotion
# ─────────────────────────────────────────────────────────────────────────────


def test_promote_candidate_makes_verify_syntax_valid(temp_db):
    """The whole point of C: a promoted candidate turns NO_VERIFICABLE → VÁLIDO."""
    from src.syntax_db import candidate_repository as cr
    from src.syntax_db.gx_repository import verify_syntax

    # Before curation the catalog is empty → the cut layer says NO_VERIFICABLE.
    assert verify_syntax("Default")["verdict"] == "NO_VERIFICABLE"

    cid = cr.store_candidate(
        name="Default",
        object_type="Rule",
        signature={"raw": "Default(att | &var, expression);"},
        description="Assigns a default value.",
        evidence="Syntax Default(att | &var, expression);",
        source_doc_id="https://wiki.genexus.com/commwiki/servlet/wiki?6850",
    )

    promoted = cr.promote_candidate(cid)
    assert promoted is not None

    verdict = verify_syntax("Default")
    assert verdict["verdict"] == "VÁLIDO"
    assert verdict["name"] == "Default"
    assert verdict["source"] == "wiki"


def test_store_candidate_dedups_and_increments_hit_count(temp_db):
    from src.syntax_db import candidate_repository as cr

    cr.store_candidate(name="Msg", object_type="Method")
    cr.store_candidate(name="Msg", object_type="Method")

    pending = cr.list_candidates(status="pending")
    assert len(pending) == 1
    assert pending[0]["hit_count"] == 2


def test_list_candidates_filters_by_status_and_reject_works(temp_db):
    from src.syntax_db import candidate_repository as cr

    a = cr.store_candidate(name="A", object_type="Property")
    cr.store_candidate(name="B", object_type="Property")

    cr.reject_candidate(a)

    pending = cr.list_candidates(status="pending")
    assert [c["name"] for c in pending] == ["B"]
    assert cr.list_candidates(status="rejected")[0]["name"] == "A"


# ─────────────────────────────────────────────────────────────────────────────
# Extractor — discovery of candidate signatures from dump blocks
# ─────────────────────────────────────────────────────────────────────────────


class _Doc:
    def __init__(self, title, content, url):
        self.title = title
        self.content = content
        self.url = url


def test_extract_candidate_from_reference_block():
    from src.syntax_db.extractor import extract_candidates

    doc = _Doc(
        title="Default",
        content=(
            "Default rule. Assigns a default value to an attribute. "
            "Syntax Default(att | &var, expression); Where att is the attribute."
        ),
        url="https://wiki.genexus.com/commwiki/servlet/wiki?6850",
    )

    drafts = extract_candidates([doc])

    assert len(drafts) == 1
    d = drafts[0]
    assert d.name == "Default"
    assert d.signature.startswith("Default(")
    assert "Where" not in d.signature  # trailing prose trimmed
    assert d.source_doc_id == doc.url
    assert d.object_type  # non-empty (required by GxSyntax)


def test_extract_skips_prose_without_syntax():
    from src.syntax_db.extractor import extract_candidates

    doc = _Doc(
        title="Introduction to WorkWithPlus and its many productivity features",
        content="This is a long guide describing how to get started, step by step.",
        url="https://docs.workwithplus.com/wiki?1",
    )

    assert extract_candidates([doc]) == []


# ─────────────────────────────────────────────────────────────────────────────
# gx_repository fix: bt_api routing must not hit the GxSyntax table
# ─────────────────────────────────────────────────────────────────────────────


def test_verify_bt_api_routes_to_bt_apis_table(temp_db):
    from src.syntax_db.models import BtApi, get_session
    from src.syntax_db.gx_repository import verify_syntax

    session = get_session()
    session.add(BtApi(endpoint="GetBalance", method="GET", path="/accounts/balance"))
    session.commit()
    session.close()

    # bt_api language must query BtApi (found) ...
    assert verify_syntax("GetBalance", language="bt_api")["verdict"] == "VÁLIDO"
    # ... while the default gx language does NOT find it in the empty GxSyntax table.
    assert verify_syntax("GetBalance", language="gx")["verdict"] == "NO_VERIFICABLE"


# ─────────────────────────────────────────────────────────────────────────────
# Tokenizer — a snippet must resolve to the NAME BEING CALLED
#
# The old tokenizer joined every word of the snippet with a space, so
# `Trim(&Variable)` became the token `"Trim Variable"` and could never equal a
# one-word `GxSyntax.name`. Every call returned NO_VERIFICABLE regardless of the
# catalog, which made the whole "cut" layer inert.
# ─────────────────────────────────────────────────────────────────────────────


def _seed(session, name, **kw):
    from src.syntax_db.models import GxSyntax

    session.add(GxSyntax(name=name, object_type=kw.pop("object_type", "Function"), **kw))
    session.commit()


def test_a_function_call_resolves_to_the_function_name_not_its_arguments(temp_db):
    from src.syntax_db.models import get_session
    from src.syntax_db.gx_repository import verify_syntax

    session = get_session()
    _seed(session, "Trim", source="wiki")
    session.close()

    result = verify_syntax("Trim(&Variable)")

    assert result["verdict"] == "VÁLIDO"
    assert result["name"] == "Trim"


def test_a_method_chain_resolves_to_the_method_not_the_receiver(temp_db):
    """`&Texto.Length()` must ask about Length, not about "Texto Length"."""
    from src.syntax_db.models import get_session
    from src.syntax_db.gx_repository import verify_syntax

    session = get_session()
    _seed(session, "Length", object_type="Method", source="wiki")
    session.close()

    assert verify_syntax("&Texto.Length()")["verdict"] == "VÁLIDO"


def test_a_bare_name_still_resolves(temp_db):
    """Regression: the simplest input has to keep working."""
    from src.syntax_db.models import get_session
    from src.syntax_db.gx_repository import verify_syntax

    session = get_session()
    _seed(session, "Len", source="wiki")
    session.close()

    assert verify_syntax("Len")["verdict"] == "VÁLIDO"


def test_an_unknown_call_is_still_not_verifiable(temp_db):
    from src.syntax_db.models import get_session
    from src.syntax_db.gx_repository import verify_syntax

    session = get_session()
    _seed(session, "Trim", source="wiki")
    session.close()

    assert verify_syntax("NoExisteEsto(&x)")["verdict"] == "NO_VERIFICABLE"


# ─────────────────────────────────────────────────────────────────────────────
# Version gate — `version_since` means "exists FROM this version onwards"
#
# The catalog is GX18-sourced while the Bantotal KBs are GX16. Returning VÁLIDO
# for a GX18-only name when the caller asked for gx16 is a FALSE VALID, which is
# worse than a false positive because nothing surfaces it: the code just fails
# to compile later. Real case: `Abs()` exists in GX18 and raises `src0294
# Unknown function` in GX16.
# ─────────────────────────────────────────────────────────────────────────────


def test_a_name_introduced_later_is_not_valid_in_an_older_version(temp_db):
    from src.syntax_db.models import get_session
    from src.syntax_db.gx_repository import verify_syntax

    session = get_session()
    _seed(session, "Abs", version_since="gx18", source="wiki")
    session.close()

    result = verify_syntax("Abs(&DifDias)", version="gx16")

    assert result["verdict"] == "INVÁLIDO"
    assert "gx18" in str(result).lower()  # the reason must name the version gap


def test_a_name_that_predates_the_asked_version_is_valid(temp_db):
    from src.syntax_db.models import get_session
    from src.syntax_db.gx_repository import verify_syntax

    session = get_session()
    _seed(session, "Trim", version_since="gx16", source="wiki")
    session.close()

    assert verify_syntax("Trim(&x)", version="gx18")["verdict"] == "VÁLIDO"


def test_the_same_version_is_valid(temp_db):
    from src.syntax_db.models import get_session
    from src.syntax_db.gx_repository import verify_syntax

    session = get_session()
    _seed(session, "Trim", version_since="gx16", source="wiki")
    session.close()

    assert verify_syntax("Trim(&x)", version="gx16")["verdict"] == "VÁLIDO"


def test_an_entry_without_version_data_answers_a_hint_not_a_verdict(temp_db):
    """Missing version data is neither a yes nor a no.

    This test used to assert VÁLIDO here, reasoning that gating on absence would
    make the catalog unusable — true, and most rows do carry no version_since.
    But the alternative it picked was a FALSE VALID against a GX16 KB, which is
    the one failure mode nothing surfaces. PISTA keeps the row usable (name,
    signature and source all reach the caller) without claiming what the catalog
    cannot prove. See gx_repository.CATALOG_SOURCE_VERSION.
    """
    from src.syntax_db.models import get_session
    from src.syntax_db.gx_repository import verify_syntax

    session = get_session()
    _seed(session, "Trim", source="wiki")  # version_since is NULL
    session.close()

    result = verify_syntax("Trim(&x)", version="gx16")

    assert result["verdict"] == "PISTA"
    # Still usable: the catalog's knowledge is not withheld, only its certainty.
    assert result["name"] == "Trim"
    assert result["source"] == "wiki"
    assert "notes" in result


def test_no_version_asked_means_no_gate(temp_db):
    from src.syntax_db.models import get_session
    from src.syntax_db.gx_repository import verify_syntax

    session = get_session()
    _seed(session, "Abs", version_since="gx18", source="wiki")
    session.close()

    assert verify_syntax("Abs(&x)")["verdict"] == "VÁLIDO"
