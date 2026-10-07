"""Bearer-token primitives shared by both HTTP surfaces.

`http_server` (public MCP) and `api` (admin Ingestor) both need to pull a bearer
token off an ASGI scope and compare it against a key set. Keeping one copy
matters more than the six lines it saves: the comparison has to stay
constant-time, and a second copy is where the `==` creeps back in.
"""

import hmac

from starlette.types import Scope


def bearer_token(scope: Scope) -> str | None:
    """Extract the Bearer token from ASGI request headers, if present."""
    for key, value in scope.get("headers", []):
        if key == b"authorization":
            raw = value.decode("latin-1")
            if raw.lower().startswith("bearer "):
                return raw[len("bearer ") :].strip()
    return None


def is_authorized(token: str | None, valid_keys: frozenset[str]) -> bool:
    """Constant-time membership check. Fails closed when no keys are configured.

    `compare_digest` over a plain `in`: the latter short-circuits on the first
    differing byte, which leaks key material one character at a time to anyone
    who can time the responses.
    """
    if not valid_keys or token is None:
        return False
    return any(hmac.compare_digest(token, key) for key in valid_keys)


def client_ip(scope: Scope) -> str:
    """TCP peer host, or "unknown" when the server did not record one."""
    client = scope.get("client")
    return client[0] if client else "unknown"
