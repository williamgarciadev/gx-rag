"""Guard for outbound fetches driven by user-supplied URLs.

Corpus ingestion takes a URL and fetches it. Without a gate that is a
server-side request forgery primitive: the caller picks any address the
*process* can reach, and this process sits on a shared Docker network next to
every other internal service. `169.254.169.254`, `127.0.0.1:8000` and
`http://other-container:8080/` are all reachable from here and none of them are
reachable from the caller — which is the whole definition of SSRF.

Two independent checks, because either one alone is bypassable:

1. **Host allowlist.** Equality against a closed set, never a substring or
   suffix test: `"wiki.genexus.com" in "wiki.genexus.com.evil.test"` is True,
   and that is the bug this module exists to not have.
2. **Resolved-address check.** The allowlist is a set of *names*, and a name is
   only a claim about an address until DNS answers. An allowlisted name whose A
   record points at 10.0.0.0/8 is the rebinding case, so every resolved address
   has to be public before the fetch is allowed.

Fails closed everywhere: an empty allowlist blocks everything, and a host that
does not resolve is blocked rather than handed to the HTTP client to retry.
"""

import ipaddress
import socket

import httpx

# Documentation sources the scraper is allowed to reach. A closed set on
# purpose: adding a host is a deliberate act, and the default must never be
# "whatever the caller typed".
DEFAULT_ALLOWED_HOSTS = frozenset(
    {
        "docs.genexus.com",
        "wiki.genexus.com",
        "sac.genexus.com",
    }
)

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class BlockedUrlError(ValueError):
    """Raised when a URL is not safe to fetch. Never carries the resolved IP."""


def _resolve(host: str) -> list[str]:
    """Every address `host` resolves to. Empty when it does not resolve.

    Seam for tests: rebinding and NXDOMAIN are the two cases that matter and
    neither can be provoked through the real resolver.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return []
    return [info[4][0] for info in infos]


def _is_public(address: str) -> bool:
    """True only for a globally routable address.

    `is_global` already excludes loopback, link-local, private ranges, shared
    address space and the reserved blocks, so it is one check instead of a
    hand-maintained list that would go stale.
    """
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


def assert_fetchable(url: str, *, allowed: frozenset[str] | None = None) -> None:
    """Raise `BlockedUrlError` unless `url` is safe for the scraper to fetch.

    Returns None on success so callers read as an assertion, not a predicate —
    a predicate invites `if is_ok(url): ...` with a missing else.
    """
    if allowed is None:
        allowed = DEFAULT_ALLOWED_HOSTS

    parsed = httpx.URL(url)

    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise BlockedUrlError(f"scheme '{scheme}' not allowed")

    host = (parsed.host or "").lower()
    # Equality against the allowlist — see the module docstring on why a
    # substring or suffix test is the vulnerability, not the fix.
    if host not in {h.lower() for h in allowed}:
        raise BlockedUrlError(f"host not allowed: {host}")

    # A literal address skips DNS; check it directly so the loopback and
    # metadata cases cannot walk past the resolver step.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        addresses = _resolve(host)
        if not addresses:
            raise BlockedUrlError(f"host does not resolve: {host}") from None
    else:
        addresses = [host]

    for address in addresses:
        if not _is_public(address):
            # Do NOT include the address: the error reaches the caller, and the
            # difference between "private address" and *which* private address
            # is exactly the internal-network map an attacker is probing for.
            raise BlockedUrlError(f"host resolves to a private address: {host}")
