"""
gx_bt_rag.http_server — public MCP transport over Streamable HTTP.

Network-facing entrypoint. It does NOT reimplement any tool: it wraps a
RESTRICTED public server (read tools only — see build_public_server) that shares
`dispatch_tool` with the stdio server, exposed over the official SDK's Streamable
HTTP transport at /mcp.

Deliberately SEPARATE from `src.api` (the admin Ingestor API). The admin surface
owns ingestion (/scrape) and must never share a process with the public MCP
endpoint.

Security posture (this module): read-only tool allowlist, bearer-token auth
(fail-closed), per-key rate limiting, per-IP auth-failure throttling, redacted
errors.

DEFERRED hardening — MANDATORY before internet exposure (Phase C, deploy):
    - Bind uvicorn to 127.0.0.1 and terminate TLS at a reverse proxy
      (Caddy/nginx). The bearer token must never travel in cleartext.
    - TrustedHostMiddleware(allowed_hosts=["gxrag.wgsoft.com.co", ...]).
    - Request body-size cap (a few hundred KB) to bound memory use.
    - Run single-worker, OR move rate-limit/failure state to Redis before
      scaling — the counters are per-process and reset at the 60s window edge
      (allows ~2x burst across a boundary).

Run locally (dev only — no TLS):
    GX_PUBLIC_API_KEYS=<32+char-key> .venv/bin/uvicorn src.http_server:app \
        --host 127.0.0.1 --port 8080
"""

import contextlib
import hmac
import logging
import time
from collections.abc import AsyncIterator

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

from src.server import (
    PUBLIC_TOOL_NAMES,
    READ_TOOLS,
    dispatch_tool,
    register_tools,
    reject_non_public_collection,
)

log = logging.getLogger(__name__)

MCP_PATH = "/mcp"
# Max accepted request body (bytes). Bounds memory use against a valid-key DoS.
MAX_BODY_BYTES = 256 * 1024


def _content_length(scope: Scope) -> int:
    """Declared Content-Length in bytes, or 0 if absent/unparseable."""
    for key, value in scope.get("headers", []):
        if key == b"content-length":
            try:
                return int(value)
            except ValueError:
                return 0
    return 0


def _bearer_token(scope: Scope) -> str | None:
    """Extract the Bearer token from the ASGI request headers, if present."""
    for key, value in scope.get("headers", []):
        if key == b"authorization":
            raw = value.decode("latin-1")
            if raw.lower().startswith("bearer "):
                return raw[len("bearer ") :].strip()
    return None


def _is_authorized(token: str | None, valid_keys: frozenset[str]) -> bool:
    """Constant-time membership check. Fails closed when no keys are configured."""
    if not valid_keys or token is None:
        return False
    return any(hmac.compare_digest(token, key) for key in valid_keys)


def _client_ip(scope: Scope) -> str:
    """TCP peer host. (Behind a reverse proxy in prod, configure trusted
    X-Forwarded-For handling so this reflects the real client, not the proxy.)"""
    client = scope.get("client")
    return client[0] if client else "unknown"


class _RateWindow:
    """In-memory fixed-window (60s) hit counter, keyed by an arbitrary string."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[int, int]] = {}

    def hit(self, key: str) -> int:
        """Increment and return the count within the current window."""
        window = int(time.time()) // 60
        stored_window, count = self._store.get(key, (window, 0))
        count = count + 1 if stored_window == window else 1
        self._store[key] = (window, count)
        return count

    def count(self, key: str) -> int:
        """Current count without incrementing."""
        window = int(time.time()) // 60
        stored_window, count = self._store.get(key, (window, 0))
        return count if stored_window == window else 0


def build_public_server() -> Server:
    """A restricted MCP server for the public transport: read tools ONLY.

    It shares `dispatch_tool` with the stdio server (one implementation), but
    structurally cannot serve write tools — they are neither listed nor
    dispatched. This is the defense-in-depth layer that survives an auth breach.
    """
    public = Server("gx-bt-rag-mcp")

    async def guarded_dispatch(name: str, arguments: dict) -> list[TextContent]:
        if name not in PUBLIC_TOOL_NAMES:
            log.warning("Blocked non-public tool on HTTP transport: %s", name)
            return [
                TextContent(
                    type="text",
                    text=f"Tool '{name}' no disponible en el endpoint público.",
                )
            ]
        # Segunda dimensión del gate: la TOOL puede ser pública y la COLECCION no.
        # search_docs / get_article / get_document reciben `collection` como
        # parámetro, así que sin este chequeo una colección privada quedaba a un
        # argumento de distancia de internet.
        if rechazo := reject_non_public_collection(arguments.get("collection")):
            log.warning(
                "Blocked non-public collection on HTTP transport: %s (tool=%s)",
                arguments.get("collection"),
                name,
            )
            return [TextContent(type="text", text=rechazo)]
        return await dispatch_tool(name, arguments)

    # Solo READ_TOOLS se listan, y guarded_dispatch vuelve a chequear al invocar:
    # la lista es la cara visible, el guard es el que realmente cierra la puerta.
    register_tools(public, READ_TOOLS, guarded_dispatch)

    return public


def create_app(
    api_keys: frozenset[str] | set[str] | None = None,
    rate_limit_per_minute: int | None = None,
    max_auth_failures_per_minute: int | None = None,
    allowed_hosts: list[str] | None = None,
) -> Starlette:
    """Build the ASGI app exposing the MCP server over Streamable HTTP.

    api_keys: the set of valid bearer tokens. When None, keys are loaded from
    config (GX_PUBLIC_API_KEYS). An empty set means the endpoint fails closed —
    every request is rejected with 401.

    rate_limit_per_minute: max requests per API key per fixed 60s window. When
    None, read from config. NOTE: the counter is in-memory and per-process, so
    with more than one instance the effective limit is per-instance — move to a
    shared store (Redis) before scaling horizontally.

    stateless=True: each request is self-contained, so the endpoint scales
    horizontally with no shared session state — the right fit for a
    request/response RAG that never pushes server-initiated messages.

    json_response=True: reply with plain JSON instead of an SSE stream, which
    keeps the public contract simple for ordinary HTTP clients.
    """
    if (
        api_keys is None
        or rate_limit_per_minute is None
        or max_auth_failures_per_minute is None
        or allowed_hosts is None
    ):
        from src.config import config

        if api_keys is None:
            api_keys = config.public_api_key_set
        if rate_limit_per_minute is None:
            rate_limit_per_minute = config.rate_limit_per_minute
        if max_auth_failures_per_minute is None:
            max_auth_failures_per_minute = config.max_auth_failures_per_minute
        if allowed_hosts is None:
            allowed_hosts = config.allowed_host_list

    valid_keys = frozenset(api_keys)
    limit = rate_limit_per_minute
    max_auth_failures = max_auth_failures_per_minute

    if not valid_keys:
        log.warning(
            "No public API keys configured — the MCP endpoint will reject all "
            "requests (fail closed). Set GX_PUBLIC_API_KEYS to enable access."
        )

    session_manager = StreamableHTTPSessionManager(
        app=build_public_server(),
        event_store=None,
        json_response=True,
        stateless=True,
    )

    # Per-API-key request throttle and per-IP auth-failure throttle (anti
    # brute-force). Both in-memory/per-process — see the docstring's scaling note.
    request_rate = _RateWindow()
    auth_failures = _RateWindow()

    async def _reject(scope, receive, send, status, error, headers=None):
        await JSONResponse({"error": error}, status_code=status, headers=headers)(
            scope, receive, send
        )

    async def handle_mcp(scope: Scope, receive: Receive, send: Send) -> None:
        ip = _client_ip(scope)
        # Stop brute-forcing BEFORE the auth check, so failed attempts are capped.
        if auth_failures.count(ip) >= max_auth_failures:
            await _reject(
                scope,
                receive,
                send,
                429,
                "too many failed attempts",
                {"Retry-After": "60"},
            )
            return

        token = _bearer_token(scope)
        if not _is_authorized(token, valid_keys):
            auth_failures.hit(ip)
            await _reject(
                scope,
                receive,
                send,
                401,
                "unauthorized",
                {"WWW-Authenticate": "Bearer"},
            )
            return

        if request_rate.hit(token) > limit:
            await _reject(
                scope, receive, send, 429, "rate limit exceeded", {"Retry-After": "60"}
            )
            return

        if _content_length(scope) > MAX_BODY_BYTES:
            await _reject(scope, receive, send, 413, "request body too large")
            return

        await session_manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        # The session manager's task group must be running for the whole app
        # lifetime; Starlette's lifespan is what keeps it alive.
        async with session_manager.run():
            log.info("MCP Streamable HTTP transport ready at %s", MCP_PATH)
            yield

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Mount(MCP_PATH, app=handle_mcp),
        ],
        middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)],
        lifespan=lifespan,
    )


# Module-level ASGI app for `uvicorn src.http_server:app`.
app = create_app()
