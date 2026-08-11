"""
Behavioral tests for the Ingestor API HTTP surface.

Focus: the error-handling contract of the retrieval/syntax endpoints.
Underlying layers (ChromaDB, syntax repository) are mocked so these tests
exercise the API's behavior, not the data stores.
"""

import pytest

pytestmark = pytest.mark.integration


def _raise(exc):
    """Return a function that raises `exc` whenever it is called."""

    def _fn(*args, **kwargs):
        raise exc

    return _fn


def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_status_lists_collections_on_success(client, monkeypatch):
    import src.vectorstore.pg_collections as pg_collections

    monkeypatch.setattr(
        pg_collections, "list_collections", lambda: ["gx_docs", "gx_syntax"]
    )

    response = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["collections"] == ["gx_docs", "gx_syntax"]
    assert body["store_error"] is None


def test_status_surfaces_store_error_instead_of_swallowing_it(client, monkeypatch):
    import src.vectorstore.pg_collections as pg_collections

    monkeypatch.setattr(
        pg_collections, "list_collections", _raise(RuntimeError("postgres down"))
    )

    response = client.get("/status")

    # Endpoint must still answer, but the failure must be visible — not silent.
    assert response.status_code == 200
    body = response.json()
    assert body["collections"] == []
    assert body["store_error"] is not None
    assert "postgres down" in body["store_error"]


def test_verify_returns_result_on_success(client, monkeypatch):
    import src.syntax_db.gx_repository as repo

    monkeypatch.setattr(repo, "verify_syntax", lambda *a, **k: "VALIDO")

    response = client.get("/verify", params={"snippet": "Msg()"})

    assert response.status_code == 200
    assert response.json()["result"] == "VALIDO"


def test_verify_returns_500_on_error(client, monkeypatch):
    import src.syntax_db.gx_repository as repo

    monkeypatch.setattr(repo, "verify_syntax", _raise(RuntimeError("db gone")))

    response = client.get("/verify", params={"snippet": "Msg()"})

    # Regression guard: this used to return HTTP 200 with an error payload.
    assert response.status_code == 500
    assert "db gone" in response.json()["detail"]


def test_methods_returns_list_on_success(client, monkeypatch):
    import src.syntax_db.gx_repository as repo

    monkeypatch.setattr(repo, "list_methods", lambda *a, **k: ["Load", "Save"])

    response = client.get("/methods", params={"object_type": "Transaction"})

    assert response.status_code == 200
    body = response.json()
    assert body["methods"] == ["Load", "Save"]
    assert body["count"] == 2


def test_methods_returns_500_on_error(client, monkeypatch):
    import src.syntax_db.gx_repository as repo

    monkeypatch.setattr(repo, "list_methods", _raise(RuntimeError("catalog missing")))

    response = client.get("/methods", params={"object_type": "Transaction"})

    # Regression guard: this used to return HTTP 200 with an empty list + error.
    assert response.status_code == 500
    assert "catalog missing" in response.json()["detail"]


def test_documents_accepts_id_query_param_and_returns_doc(client, monkeypatch):
    import src.vectorstore.pg_collections as collections

    monkeypatch.setattr(
        collections,
        "get_document",
        lambda doc_id, collection="gx_docs": {"id": doc_id, "text": "hola"},
    )

    # The public contract is ?id= even though the handler param is doc_id.
    response = client.get("/documents", params={"id": "abc123"})

    assert response.status_code == 200
    assert response.json()["id"] == "abc123"


def test_documents_returns_404_when_missing(client, monkeypatch):
    import src.vectorstore.pg_collections as collections

    monkeypatch.setattr(
        collections, "get_document", lambda doc_id, collection="gx_docs": None
    )

    response = client.get("/documents", params={"id": "nope"})

    assert response.status_code == 404


def test_article_reassembles_and_returns_whole_doc(client, monkeypatch):
    import src.vectorstore.pg_collections as collections

    monkeypatch.setattr(
        collections,
        "get_article",
        lambda article_id, collection="gx_docs": {
            "id": article_id,
            "content": "first\n\nsecond",
            "chunks": 2,
        },
    )

    response = client.get("/article", params={"id": "art"})

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "art"
    assert body["chunks"] == 2


def test_article_returns_404_when_missing(client, monkeypatch):
    import src.vectorstore.pg_collections as collections

    monkeypatch.setattr(
        collections, "get_article", lambda article_id, collection="gx_docs": None
    )

    response = client.get("/article", params={"id": "nope"})

    assert response.status_code == 404
