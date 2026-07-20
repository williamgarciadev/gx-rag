"""Shared pytest fixtures for the gx-rag test suite."""

import pytest
from fastapi.testclient import TestClient

from src.api import app


@pytest.fixture
def client():
    """FastAPI TestClient bound to the Ingestor API app.

    raise_server_exceptions=False so that unhandled errors are returned as the
    app's 500 response (via the global exception handler) instead of being
    re-raised into the test — letting us assert on the 500 contract.
    """
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
