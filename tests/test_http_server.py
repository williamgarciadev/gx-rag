"""
Behavioral tests for the public Streamable HTTP transport of the MCP server.

Focus: the transport is mounted at /mcp and speaks MCP over HTTP — a client can
complete the `initialize` handshake and the server identifies itself. The tool
logic itself is covered by the stdio server tests; here we only assert that the
HTTP transport wraps the SAME server without reimplementing tools.
"""

import json

import pytest
from starlette.testclient import TestClient

from src.http_server import create_app

pytestmark = pytest.mark.integration

# A minimal MCP initialize request. Streamable HTTP requires the client to accept
# both JSON and SSE; the server (json_response=True) answers with plain JSON.
_INIT_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "pytest-client", "version": "0.0.0"},
    },
}

TEST_API_KEY = "test-key-abc123"

_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "Authorization": f"Bearer {TEST_API_KEY}",
}


@pytest.fixture
def mcp_client():
    """TestClient bound to the Streamable HTTP MCP app.

    The `with` block drives Starlette's lifespan, which starts the session
    manager's task group — without it the transport is not running. A known API
    key is injected so authenticated requests can exercise the transport.
    """
    with TestClient(create_app(api_keys={TEST_API_KEY})) as client:
        yield client


def test_mcp_endpoint_is_mounted(mcp_client):
    """A bare GET without a session must be rejected, proving /mcp is live."""
    response = mcp_client.get("/mcp")

    # Streamable HTTP rejects a GET that has no established session; the exact
    # 4xx is an implementation detail — what matters is the route exists and the
    # transport is validating rather than 404-ing.
    assert response.status_code != 404


def test_initialize_handshake_returns_server_identity(mcp_client):
    """A valid initialize handshake identifies the server as gx-bt-rag-mcp."""
    response = mcp_client.post("/mcp", headers=_MCP_HEADERS, json=_INIT_REQUEST)

    assert response.status_code == 200

    body = _parse_mcp_body(response)
    result = body["result"]
    assert result["serverInfo"]["name"] == "gx-bt-rag-mcp"


def test_request_without_bearer_is_rejected(mcp_client):
    """No Authorization header → 401. Fail closed on the public surface."""
    headers = {k: v for k, v in _MCP_HEADERS.items() if k != "Authorization"}

    response = mcp_client.post("/mcp/", headers=headers, json=_INIT_REQUEST)

    assert response.status_code == 401


def test_request_with_invalid_bearer_is_rejected(mcp_client):
    """A wrong API key → 401, never reaching the MCP transport."""
    headers = {**_MCP_HEADERS, "Authorization": "Bearer not-a-real-key"}

    response = mcp_client.post("/mcp/", headers=headers, json=_INIT_REQUEST)

    assert response.status_code == 401


def test_request_with_valid_bearer_is_accepted(mcp_client):
    """A valid API key completes the handshake."""
    response = mcp_client.post("/mcp/", headers=_MCP_HEADERS, json=_INIT_REQUEST)

    assert response.status_code == 200
    assert _parse_mcp_body(response)["result"]["serverInfo"]["name"] == "gx-bt-rag-mcp"


def test_app_with_no_keys_configured_denies_everything(monkeypatch):
    """Fail closed: if no API keys are configured, the endpoint rejects all."""
    with TestClient(create_app(api_keys=set())) as client:
        response = client.post("/mcp/", headers=_MCP_HEADERS, json=_INIT_REQUEST)

    assert response.status_code == 401


def test_rate_limit_returns_429_when_exceeded():
    """Requests beyond the per-minute budget are throttled with 429."""
    limit = 2
    with TestClient(
        create_app(api_keys={TEST_API_KEY}, rate_limit_per_minute=limit)
    ) as client:
        codes = [
            client.post("/mcp/", headers=_MCP_HEADERS, json=_INIT_REQUEST).status_code
            for _ in range(limit + 1)
        ]

    assert codes[:limit] == [200] * limit
    assert codes[limit] == 429


def test_health_endpoint_is_public(mcp_client):
    """/health answers 200 without auth — for Docker/Traefik health probes."""
    response = mcp_client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_untrusted_host_header_is_rejected():
    """TrustedHostMiddleware blocks Host headers outside the allowlist."""
    with TestClient(
        create_app(api_keys={TEST_API_KEY}, allowed_hosts=["gxrag.wgsoft.com.co"])
    ) as client:
        # TestClient sends Host: testserver, which is NOT in the allowlist.
        response = client.post("/mcp/", headers=_MCP_HEADERS, json=_INIT_REQUEST)

    assert response.status_code == 400


def test_oversized_request_body_is_rejected():
    """A body over the size cap is refused with 413, even with a valid key."""
    huge = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"padding": "A" * 300_000},
    }
    with TestClient(create_app(api_keys={TEST_API_KEY})) as client:
        response = client.post("/mcp/", headers=_MCP_HEADERS, json=huge)

    assert response.status_code == 413


def test_config_rejects_weak_api_keys():
    """Weak (short) bearer keys must be refused at config load (LOW)."""
    from src.config import Config

    cfg = Config(public_api_keys="short")
    with pytest.raises(ValueError):
        _ = cfg.public_api_key_set


def test_config_accepts_strong_api_keys():
    from src.config import Config

    strong = "a" * 32
    cfg = Config(public_api_keys=strong)
    assert cfg.public_api_key_set == frozenset({strong})


def test_repeated_auth_failures_are_throttled():
    """Brute-forcing keys must hit an IP-keyed 429, not endless 401s (C1)."""
    with TestClient(
        create_app(api_keys={TEST_API_KEY}, max_auth_failures_per_minute=2)
    ) as client:
        bad = {**_MCP_HEADERS, "Authorization": "Bearer guess"}
        codes = [
            client.post("/mcp/", headers=bad, json=_INIT_REQUEST).status_code
            for _ in range(3)
        ]

    # First failures answer 401; once the IP failure budget is spent → 429.
    assert codes[:2] == [401, 401]
    assert codes[2] == 429


def test_public_tool_error_does_not_leak_exception_detail(mcp_client, monkeypatch):
    """A tool failure must NOT return raw exception text to public callers (H1)."""
    import src.vectorstore.retriever as retriever

    secret = "/srv/secret/path/creds.db"
    monkeypatch.setattr(
        retriever, "hybrid_search", _raise(RuntimeError(f"boom {secret}"))
    )

    body = _mcp_call(
        mcp_client,
        "tools/call",
        {"name": "search_docs", "arguments": {"query": "x"}},
    )

    text = json.dumps(body)
    assert secret not in text, "internal exception detail leaked to public caller"
    assert "boom" not in text


def test_rate_limit_is_per_key():
    """One key exhausting its budget must not throttle a different key."""
    with TestClient(
        create_app(api_keys={"k1", "k2"}, rate_limit_per_minute=1)
    ) as client:
        h1 = {**_MCP_HEADERS, "Authorization": "Bearer k1"}
        h2 = {**_MCP_HEADERS, "Authorization": "Bearer k2"}

        assert client.post("/mcp/", headers=h1, json=_INIT_REQUEST).status_code == 200
        assert client.post("/mcp/", headers=h1, json=_INIT_REQUEST).status_code == 429
        # k2 has its own bucket — unaffected by k1 exhausting its budget.
        assert client.post("/mcp/", headers=h2, json=_INIT_REQUEST).status_code == 200


READ_TOOL_NAMES = {
    "search_docs",
    "get_article",
    "get_document",
    "verify_syntax",
    "list_methods",
}
WRITE_TOOL_NAMES = {"add_from_url", "ingest_batch"}


def _mcp_call(client, method, params, req_id=1):
    """POST an MCP JSON-RPC request to /mcp/ and return the parsed payload."""
    response = client.post(
        "/mcp/",
        headers=_MCP_HEADERS,
        json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params},
    )
    assert response.status_code == 200, response.text
    return _parse_mcp_body(response)


def test_public_endpoint_lists_only_read_tools(mcp_client):
    """The public surface must expose read tools ONLY — never write tools.

    Defense in depth: even before auth, add_from_url (SSRF) and ingest_batch
    must not exist on the internet-facing transport.
    """
    body = _mcp_call(mcp_client, "tools/list", {})

    names = {t["name"] for t in body["result"]["tools"]}
    assert names == READ_TOOL_NAMES
    assert not (names & WRITE_TOOL_NAMES), "write tools leaked onto public surface"


def test_public_endpoint_refuses_to_execute_write_tool(mcp_client, monkeypatch):
    """Calling a write tool on the public endpoint must NOT run it."""
    import scripts.scrape_wiki as scraper

    monkeypatch.setattr(
        scraper,
        "scrape",
        _raise(AssertionError("scrape must never run on the public endpoint")),
    )

    body = _mcp_call(
        mcp_client,
        "tools/call",
        {"name": "add_from_url", "arguments": {"url": "http://evil.internal/x"}},
    )

    # The transport answered, but the tool was rejected — not executed.
    text = json.dumps(body).lower()
    assert (
        "result" not in body
        or body.get("result", {}).get("isError")
        or "no disponible" in text
    )


def _raise(exc):
    def _fn(*args, **kwargs):
        raise exc

    return _fn


def _parse_mcp_body(response):
    """Return the JSON-RPC payload whether the server replied JSON or SSE."""
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("text/event-stream"):
        # SSE frame: lines like "data: {...}"; grab the first data payload.
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())
        raise AssertionError(f"No SSE data frame in response: {response.text!r}")
    return response.json()
