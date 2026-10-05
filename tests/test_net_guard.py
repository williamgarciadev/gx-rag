"""Behavioral tests for the outbound-fetch guard.

`fetch_article` used to accept any URL and follow redirects blindly, which made
the corpus ingestion endpoints an SSRF proxy into whatever network the process
sits on. These tests pin the guard that closes it.
"""

import pytest

from src.net_guard import BlockedUrlError, assert_fetchable

pytestmark = pytest.mark.unit


ALLOWED = frozenset({"docs.genexus.com", "wiki.genexus.com"})


def test_allowlisted_host_is_accepted():
    assert_fetchable("https://wiki.genexus.com/commwiki/wiki?8196", allowed=ALLOWED)


def test_host_outside_the_allowlist_is_rejected():
    with pytest.raises(BlockedUrlError, match="host not allowed"):
        assert_fetchable("https://evil.example.com/payload", allowed=ALLOWED)


def test_subdomain_of_an_allowlisted_host_is_not_implicitly_allowed():
    # "wiki.genexus.com.evil.test" ends with an allowlisted label but is a
    # different site; a suffix check instead of an equality check would pass it.
    with pytest.raises(BlockedUrlError, match="host not allowed"):
        assert_fetchable("https://wiki.genexus.com.evil.test/x", allowed=ALLOWED)


def test_host_comparison_ignores_case_and_default_port():
    assert_fetchable("https://WIKI.GeneXus.COM:443/wiki?1", allowed=ALLOWED)


def test_non_http_scheme_is_rejected():
    with pytest.raises(BlockedUrlError, match="scheme"):
        assert_fetchable("file:///etc/passwd", allowed=ALLOWED)


def test_literal_loopback_address_is_rejected_even_if_allowlisted():
    # The cloud-metadata / internal-service case: an operator who adds a host
    # that resolves internally must still not reach it.
    with pytest.raises(BlockedUrlError, match="private address"):
        assert_fetchable(
            "http://127.0.0.1:8000/scrape",
            allowed=frozenset({"127.0.0.1"}),
        )


def test_link_local_metadata_address_is_rejected():
    with pytest.raises(BlockedUrlError, match="private address"):
        assert_fetchable(
            "http://169.254.169.254/latest/meta-data/",
            allowed=frozenset({"169.254.169.254"}),
        )


def test_hostname_resolving_to_a_private_address_is_rejected(monkeypatch):
    # DNS rebinding: the name is on the allowlist, the A record points inside.
    monkeypatch.setattr(
        "src.net_guard._resolve", lambda host: ["10.0.0.7"]
    )

    with pytest.raises(BlockedUrlError, match="private address"):
        assert_fetchable("https://wiki.genexus.com/wiki?1", allowed=ALLOWED)


def test_a_host_that_does_not_resolve_is_rejected(monkeypatch):
    monkeypatch.setattr("src.net_guard._resolve", lambda host: [])

    with pytest.raises(BlockedUrlError, match="does not resolve"):
        assert_fetchable("https://wiki.genexus.com/wiki?1", allowed=ALLOWED)


def test_empty_allowlist_blocks_everything():
    # Fail closed: a misconfigured allowlist must not mean "allow all".
    with pytest.raises(BlockedUrlError, match="host not allowed"):
        assert_fetchable("https://wiki.genexus.com/wiki?1", allowed=frozenset())
