"""Tests for splitting a collection by source, without re-embedding.

Re-embedding 28k chunks costs hours of CPU; the migration must carry the
existing vectors across. It must also be safe to re-run — a half-finished move
left by a crash has to converge, not duplicate.
"""

import chromadb
import pytest

from src.vectorstore.migrations import (
    count_matching_by_metadata,
    delete_matching_by_metadata,
    move_matching_by_metadata,
)


@pytest.fixture
def client(tmp_path):
    return chromadb.PersistentClient(path=str(tmp_path / "chroma"))


def _seed(client):
    col = client.get_or_create_collection("source")
    col.add(
        ids=["sac_1", "sac_2", "wiki_1"],
        embeddings=[[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]],
        documents=["ticket uno", "ticket dos", "articulo wiki"],
        metadatas=[
            {"url": "https://sac.genexus.com/frontend?1", "title": "SAC #1"},
            {"url": "https://sac.genexus.com/frontend?2", "title": "SAC #2"},
            {"url": "https://wiki.genexus.com/wiki?9", "title": "For Each"},
        ],
    )
    return col


def test_moves_only_matching_chunks_and_keeps_their_embeddings(client):
    _seed(client)

    moved = move_matching_by_metadata(
        client, "source", "target", field="url", contains="sac.genexus.com"
    )

    target = client.get_collection("target")
    got = target.get(include=["embeddings", "documents", "metadatas"])
    assert moved == 2
    assert sorted(got["ids"]) == ["sac_1", "sac_2"]
    by_id = dict(zip(got["ids"], [list(e) for e in got["embeddings"]]))
    assert by_id["sac_1"] == pytest.approx([0.1, 0.2])
    assert by_id["sac_2"] == pytest.approx([0.3, 0.4])


def test_non_matching_chunks_stay_in_the_source(client):
    _seed(client)

    move_matching_by_metadata(
        client, "source", "target", field="url", contains="sac.genexus.com"
    )

    remaining = client.get_collection("source").get()
    assert remaining["ids"] == ["wiki_1"]


def test_counting_reports_matches_without_moving_anything(client):
    _seed(client)

    n = count_matching_by_metadata(
        client, "source", field="url", contains="sac.genexus.com"
    )

    assert n == 2
    assert client.get_collection("source").count() == 3
    assert "target" not in [c.name for c in client.list_collections()]


def test_rerunning_the_migration_is_idempotent(client):
    _seed(client)
    kwargs = dict(field="url", contains="sac.genexus.com")

    first = move_matching_by_metadata(client, "source", "target", **kwargs)
    second = move_matching_by_metadata(client, "source", "target", **kwargs)

    assert (first, second) == (2, 0)
    assert client.get_collection("target").count() == 2
    assert client.get_collection("source").count() == 1


# ─── Deleting (no target collection) ─────────────────────────────────────────
#
# Dropping a manual that the DB MCP already answers better (MDU-99000) has no
# destination — it is removal, not a move. Chunks are matched on the "id"
# metadata field, which holds the source_key ("file:///...MDU-99000-....pdf#page=N").


def _seed_manuals(client):
    col = client.get_or_create_collection("bt_docs")
    col.add(
        ids=["m99_1", "m99_2", "prestamos_1"],
        embeddings=[[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]],
        documents=["inventario de tablas", "FSD010 campos", "altura de mora"],
        metadatas=[
            {"id": "file:///d/MDU-99000-GL-V2R3.4.pdf#page=1"},
            {"id": "file:///d/MDU-99000-GL-V2R3.4.pdf#page=2"},
            {"id": "file:///d/MDI-10201-GL-V3R1.20.pdf#page=40"},
        ],
    )
    return col


def test_deletes_only_matching_chunks(client):
    _seed_manuals(client)

    deleted = delete_matching_by_metadata(
        client, "bt_docs", field="id", contains="MDU-99000"
    )

    remaining = client.get_collection("bt_docs").get()
    assert deleted == 2
    assert remaining["ids"] == ["prestamos_1"]


def test_rerunning_the_delete_is_idempotent(client):
    _seed_manuals(client)
    kwargs = dict(field="id", contains="MDU-99000")

    first = delete_matching_by_metadata(client, "bt_docs", **kwargs)
    second = delete_matching_by_metadata(client, "bt_docs", **kwargs)

    assert (first, second) == (2, 0)
    assert client.get_collection("bt_docs").count() == 1


def test_delete_reports_progress_as_it_goes(client):
    _seed_manuals(client)
    seen: list[int] = []

    delete_matching_by_metadata(
        client, "bt_docs", field="id", contains="MDU-99000", on_progress=seen.append
    )

    assert seen == [2]


def test_counting_previews_a_delete_without_touching_anything(client):
    _seed_manuals(client)

    n = count_matching_by_metadata(client, "bt_docs", field="id", contains="MDU-99000")

    assert n == 2
    assert client.get_collection("bt_docs").count() == 3
