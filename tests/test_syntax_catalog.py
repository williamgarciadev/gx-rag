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
