"""Authentication contract of the admin Ingestor API.

The API owns `/scrape` (writes to the corpus, fetches URLs) and had no auth at
all while sitting on `infra_gateway`, an `external: true` Docker network shared
with every other service on the host. Any neighbouring container could drive it.

The gate is shaped around that threat rather than around a purist "deny
everything by default": a neighbouring container never arrives over loopback, so
loopback stays open when no key is configured and local development keeps
working untouched. Configure a key and the gate applies to everyone.
"""

import pytest
from fastapi.testclient import TestClient

from src.api import app

pytestmark = pytest.mark.integration

KEY = "k" * 32
NEIGHBOUR = ("172.18.0.9", 54321)  # another container on the shared network
LOOPBACK = ("127.0.0.1", 54321)


def _client(peer, keys):
    """TestClient whose requests appear to come from `peer`, with `keys` set."""
    app.state.admin_api_keys = frozenset(keys)
    return TestClient(app, client=peer, raise_server_exceptions=False)


def test_health_is_open_without_a_key(client):
    # The container healthcheck curls this; gating it would fail the deploy.
    assert client.get("/health").status_code == 200


def test_no_key_configured_allows_loopback(client):
    with _client(LOOPBACK, []) as c:
        assert c.get("/status").status_code != 401


def test_no_key_configured_rejects_a_neighbouring_container(client):
    with _client(NEIGHBOUR, []) as c:
        response = c.get("/status")

    assert response.status_code == 401


def test_configured_key_is_required_even_from_loopback(client):
    with _client(LOOPBACK, [KEY]) as c:
        assert c.get("/status").status_code == 401


def test_configured_key_grants_access(client):
    with _client(NEIGHBOUR, [KEY]) as c:
        response = c.get("/status", headers={"Authorization": f"Bearer {KEY}"})

    assert response.status_code != 401


def test_a_wrong_key_is_rejected(client):
    with _client(NEIGHBOUR, [KEY]) as c:
        response = c.get("/status", headers={"Authorization": f"Bearer {'x' * 32}"})

    assert response.status_code == 401


def test_scrape_is_gated(client):
    # The endpoint that writes to the corpus must never be the open one.
    with _client(NEIGHBOUR, [KEY]) as c:
        response = c.post("/scrape", json={"url": "https://wiki.genexus.com/wiki?1"})

    assert response.status_code == 401
