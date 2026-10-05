"""`fetch_article` must refuse to be an SSRF proxy.

The guard lives in `src.net_guard`; these tests pin that the scraper actually
*calls* it — on the initial URL and on every redirect hop, which is the half
that a naive `follow_redirects=True` gets wrong.
"""

import httpx
import pytest

from scripts import scrape_wiki
from src.net_guard import BlockedUrlError

pytestmark = pytest.mark.unit


class _RecordingClient:
    """httpx.Client stand-in that answers from a scripted map of url -> response."""

    requested: list[str] = []

    def __init__(self, responses):
        self._responses = responses

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, headers=None, follow_redirects=False):
        assert follow_redirects is False, (
            "redirects must be followed manually so each hop is re-validated"
        )
        type(self).requested.append(str(url))
        return self._responses[str(url)]


def _response(status, *, location=None, text="<html><body>ok</body></html>"):
    headers = {"location": location} if location else {}
    return httpx.Response(
        status_code=status,
        headers=headers,
        text=text,
        request=httpx.Request("GET", "https://wiki.genexus.com/"),
    )


@pytest.fixture(autouse=True)
def _reset_recorder():
    _RecordingClient.requested = []
    yield
    _RecordingClient.requested = []


def test_disallowed_host_is_rejected_without_any_network_call(monkeypatch):
    monkeypatch.setattr(scrape_wiki.httpx, "Client", _RecordingClient({}))

    with pytest.raises(BlockedUrlError):
        scrape_wiki.fetch_article("http://169.254.169.254/latest/meta-data/")

    # The point of a guard is that the packet never leaves.
    assert _RecordingClient.requested == []


def test_allowlisted_host_is_fetched(monkeypatch):
    url = "https://wiki.genexus.com/commwiki/wiki?8196"
    monkeypatch.setattr(
        scrape_wiki.httpx, "Client", _RecordingClient({url: _response(200)})
    )

    resp = scrape_wiki.fetch_article(url)

    assert resp.status_code == 200
    assert _RecordingClient.requested == [url]


def test_a_redirect_into_an_internal_host_is_not_followed(monkeypatch):
    # The bypass that an allowlist alone does not stop: the allowlisted origin
    # answers 302 and points at the metadata service.
    url = "https://wiki.genexus.com/commwiki/wiki?1"
    monkeypatch.setattr(
        scrape_wiki.httpx,
        "Client",
        _RecordingClient(
            {url: _response(302, location="http://169.254.169.254/latest/")}
        ),
    )

    with pytest.raises(BlockedUrlError):
        scrape_wiki.fetch_article(url)

    assert _RecordingClient.requested == [url]  # the hop was never taken


def test_a_redirect_within_the_allowlist_is_followed(monkeypatch):
    first = "https://wiki.genexus.com/commwiki/wiki?1"
    second = "https://docs.genexus.com/en/wiki?1"
    monkeypatch.setattr(
        scrape_wiki.httpx,
        "Client",
        _RecordingClient(
            {first: _response(302, location=second), second: _response(200)}
        ),
    )

    resp = scrape_wiki.fetch_article(first)

    assert resp.status_code == 200
    assert _RecordingClient.requested == [first, second]


def test_a_redirect_loop_terminates(monkeypatch):
    a = "https://wiki.genexus.com/a"
    b = "https://docs.genexus.com/b"
    monkeypatch.setattr(
        scrape_wiki.httpx,
        "Client",
        _RecordingClient({a: _response(302, location=b), b: _response(302, location=a)}),
    )

    with pytest.raises(BlockedUrlError, match="too many redirects"):
        scrape_wiki.fetch_article(a)
