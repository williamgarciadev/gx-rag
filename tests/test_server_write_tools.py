"""Behavioral tests for the MCP write tools and the verdict guidance.

Two properties are locked down here, both of which had regressed before:

1. `add_from_url` must HONOR the `collection` argument. Until 2026-08-11 the
   schema declared it and the handler never read it, so the tool reported
   writing where the caller asked while it actually wrote where classify_genre
   decided. A silent routing lie.
2. Every verify_syntax verdict must ship next-step guidance. The consumer is an
   agent, and a bare NO_VERIFICABLE gets read as "does not exist" — which is the
   single most expensive misreading this server can cause.

No network, no embeddings, no Postgres: load_dump is injected/monkeypatched.
"""

import pytest

from src import server as srv

pytestmark = pytest.mark.unit


WWP_DUMP = """Extração: https://docs.workwithplus.com/wiki?1639
================================================================================

--- URL: https://docs.workwithplus.com/wiki?1149,Options+on+Tab+Items, ---

Options on Tab Items
Options available on the Tab items of a WorkWithPlus instance.
Page Id
1149

================================================================================

--- URL: https://docs.workwithplus.com/wiki?230,Automatic+Conditions, ---

Automatic Conditions
WorkWithPlus generates conditions automatically from the transaction attributes.
Page Id
230

================================================================================
"""


@pytest.fixture
def dump(tmp_path):
    p = tmp_path / "wwp.txt"
    p.write_text(WWP_DUMP, encoding="utf-8")
    return str(p)


# ─────────────────────────────────────────────────────────────────────────────
# Schema invariants — the schema and the runtime check must not drift apart
# ─────────────────────────────────────────────────────────────────────────────


def _tool(name):
    return next(t for t in srv.WRITE_TOOLS if t.name == name)


def test_add_from_url_enum_matches_the_runtime_whitelist():
    """The declared enum IS the validated set — one source of truth."""
    enum = _tool("add_from_url").input_schema["properties"]["collection"]["enum"]
    assert set(enum) == set(srv.WRITABLE_COLLECTIONS)


def test_ingest_batch_defaults_to_dry_run():
    """Omitting dry_run must take the branch that writes nothing."""
    schema = _tool("ingest_batch").input_schema
    assert schema["properties"]["dry_run"]["default"] is True
    assert set(schema["required"]) == {"file", "format"}
    assert set(schema["properties"]["format"]["enum"]) == set(srv.DUMP_FORMATS)


def test_write_tools_stay_off_the_public_transport():
    """Ingestion mutates the corpus: it must never be internet-reachable."""
    assert srv.PUBLIC_TOOL_NAMES.isdisjoint({t.name for t in srv.WRITE_TOOLS})


# ─────────────────────────────────────────────────────────────────────────────
# ingest_batch — dry run reports without writing
# ─────────────────────────────────────────────────────────────────────────────


def test_dry_run_reports_counts_and_writes_nothing(dump, monkeypatch):
    from src.ingestion import bulk_loader

    def _explode(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("dry run must not index anything")

    monkeypatch.setattr(bulk_loader, "load_dump", _explode)

    out = srv._run_ingest_batch({"file": dump, "format": "workwithplus"})

    assert "NADA SE ESCRIBIÓ" in out
    assert "documentos : 2" in out
    # The samples must carry provenance so the caller can eyeball what lands.
    assert "1149" in out and "Options on Tab Items" in out
    # And it must state the exact call that commits.
    assert "dry_run=false" in out


def test_limit_caps_the_documents_parsed(dump):
    out = srv._run_ingest_batch(
        {"file": dump, "format": "workwithplus", "limit": 1}
    )
    assert "documentos : 1" in out


def test_wrong_format_explains_the_likely_cause(dump):
    """A mute "0 results" would send the caller hunting the wrong problem."""
    out = srv._run_ingest_batch({"file": dump, "format": "genexus"})

    assert "0 documentos" in out
    assert "otro formato" in out


def test_write_path_delegates_to_load_dump(dump, monkeypatch):
    from src.ingestion import bulk_loader

    seen = {}

    def fake_load_dump(docs, collection="gx_docs", **kwargs):
        seen["collection"] = collection
        seen["docs"] = list(docs)
        report = bulk_loader.LoadReport()
        report.record("indexed")
        report.record("skipped")
        return report

    monkeypatch.setattr(bulk_loader, "load_dump", fake_load_dump)

    out = srv._run_ingest_batch(
        {"file": dump, "format": "workwithplus", "dry_run": False}
    )

    assert seen["collection"] == "gx_docs"
    assert len(seen["docs"]) == 2
    assert "total=2" in out and "indexed=1" in out and "skipped=1" in out
    # 'skipped' is meaningless to the caller unless we say what it means.
    assert "no se re-embeddeó" in out


# ─────────────────────────────────────────────────────────────────────────────
# ingest_batch — argument validation fails fast, before touching the disk
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "args, expected",
    [
        ({"format": "genexus"}, "Falta 'file'"),
        ({"file": "x", "format": "nope"}, "inválido"),
        ({"file": "x", "format": "genexus", "collection": "bt_docs"}, "inválida"),
    ],
)
def test_bad_arguments_raise_valueerror_with_the_valid_options(args, expected):
    """A generic error would leave an agent unable to tell a typo from an outage."""
    with pytest.raises(ValueError, match=expected):
        srv._run_ingest_batch(args)


def test_missing_file_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        srv._run_ingest_batch(
            {"file": str(tmp_path / "ghost.txt"), "format": "genexus"}
        )


# ─────────────────────────────────────────────────────────────────────────────
# verify_syntax guidance — how a verdict must NOT be read
# ─────────────────────────────────────────────────────────────────────────────


def test_every_verdict_has_guidance():
    from src.syntax_db.gx_repository import Verdict

    for verdict in Verdict.__args__:
        assert verdict in srv._VERDICT_GUIDANCE, f"{verdict} has no guidance"


def test_no_verificable_says_it_is_not_a_denial():
    text = srv._verdict_guidance({"verdict": "NO_VERIFICABLE"})
    assert "NO SÉ" in text
    assert "no existe" in text  # spelled out as what it does NOT mean


def test_invalido_is_presented_as_a_typo_hint_not_proof():
    text = srv._verdict_guidance({"verdict": "INVÁLIDO"})
    assert "NO es prueba" in text
    assert "sugerencias" in text


@pytest.mark.parametrize("payload", [None, "VÁLIDO", {}, {"verdict": "???"}, 42])
def test_unknown_payload_degrades_to_non_conclusive(payload):
    """A malformed verdict must never read as a confirmation."""
    text = srv._verdict_guidance(payload)
    assert "no concluyente" in text.lower()


# ─────────────────────────────────────────────────────────────────────────────
# The upsert must be able to MOVE an article between collections
# ─────────────────────────────────────────────────────────────────────────────


def test_upsert_updates_the_collection_column():
    """Honoring `collection` in the handler is useless if the SQL drops it.

    chunk_id is derived from article_id + index, so ON CONFLICT means "same
    article again". Without `collection` in the DO UPDATE list, re-indexing an
    already-stored article into another collection left the rows where they
    were while the tool reported success — measured on the WWP article 1149.
    """
    from src.vectorstore.pg_indexer import _UPSERT_SQL

    _, do_update = _UPSERT_SQL.split("DO UPDATE SET", 1)
    assert "collection" in do_update, "the upsert cannot move an article"
    # And the conflict target stays chunk_id — the whole reasoning rests on it.
    assert "ON CONFLICT (chunk_id)" in _UPSERT_SQL
