"""Tests for the ingestion manifest — the record of what is in the corpus, and why.

Without it the decision is only in someone's head: MDU-99000 sat in a folder
literally named `_skipped/` and *was* indexed, while a manual in the main folder
was not. The next directory sweep would silently re-add what was just removed.
"""

import json

import pytest

pytestmark = pytest.mark.unit

from src.ingestion.manifest import iter_manifest_paths, load_manifest


def _write(tmp_path, documents, root="docs"):
    (tmp_path / "docs" / "Bantotal").mkdir(parents=True, exist_ok=True)
    manifest = tmp_path / "manuals.json"
    manifest.write_text(
        json.dumps({"root": root, "collection": "bt_docs", "documents": documents}),
        encoding="utf-8",
    )
    return manifest


def test_only_documents_flagged_for_ingestion_are_yielded(tmp_path):
    manifest = _write(
        tmp_path,
        [
            {"file": "Bantotal/keep.pdf", "ingest": True, "reason": "manual funcional"},
            {"file": "Bantotal/drop.pdf", "ingest": False, "reason": "lo cubre el MCP"},
        ],
    )
    for name in ("keep.pdf", "drop.pdf"):
        (tmp_path / "docs" / "Bantotal" / name).write_bytes(b"%PDF-1.4")

    paths = list(iter_manifest_paths(manifest, root=tmp_path / "docs"))

    assert [p.name for p in paths] == ["keep.pdf"]


def test_a_declared_file_that_is_missing_fails_loudly(tmp_path):
    # Silently skipping it would report a successful ingestion of nothing.
    manifest = _write(
        tmp_path, [{"file": "Bantotal/ausente.pdf", "ingest": True, "reason": "x"}]
    )

    with pytest.raises(FileNotFoundError, match="ausente.pdf"):
        list(iter_manifest_paths(manifest, root=tmp_path / "docs"))


def test_every_document_must_state_a_reason(tmp_path):
    # The reason is the whole point: an entry without one records no decision.
    manifest = _write(tmp_path, [{"file": "Bantotal/x.pdf", "ingest": False}])

    with pytest.raises(ValueError, match="reason"):
        load_manifest(manifest)


def test_the_manifest_declares_its_target_collection(tmp_path):
    manifest = _write(tmp_path, [])

    assert load_manifest(manifest).collection == "bt_docs"
