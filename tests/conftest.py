"""Shared pytest fixtures for the gx-rag test suite."""

import pytest
from fastapi.testclient import TestClient

from src.api import app

# TestClient's default peer is the literal string "testclient", which is not a
# loopback address — and the admin API only serves loopback when no key is
# configured. Presenting as 127.0.0.1 models the local curator these tests are
# about; the auth gate itself is exercised in test_api_auth.py.
LOCAL_PEER = ("127.0.0.1", 50000)


@pytest.fixture(autouse=True)
def _no_admin_keys():
    """Reset the admin key set around every test.

    It lives on `app.state` (module-level singleton), so a test that configures
    a key would otherwise leak it into every test that runs afterwards.
    """
    previous = getattr(app.state, "admin_api_keys", frozenset())
    app.state.admin_api_keys = frozenset()
    yield
    app.state.admin_api_keys = previous


@pytest.fixture
def client():
    """FastAPI TestClient bound to the Ingestor API app.

    raise_server_exceptions=False so that unhandled errors are returned as the
    app's 500 response (via the global exception handler) instead of being
    re-raised into the test — letting us assert on the 500 contract.
    """
    with TestClient(
        app, client=LOCAL_PEER, raise_server_exceptions=False
    ) as test_client:
        yield test_client
