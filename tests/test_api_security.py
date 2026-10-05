"""Security contract of the admin Ingestor API.

Two holes this pins shut:

- CORS was `allow_origins=["*"]` together with `allow_credentials=True`.
  Starlette does not send a literal `*` in that combination — it echoes the
  request's own Origin back — so every website a curator visited could drive
  this API with their credentials attached.
- `/scrape` turned a blocked URL into an opaque 500 through the catch-all
  handler, which reads as "the server broke" instead of "you asked for
  something you may not have".
"""

import pytest

pytestmark = pytest.mark.integration

ROGUE = "https://evil.example.com"


def test_a_rogue_origin_is_not_echoed_back(client):
    response = client.get("/health", headers={"Origin": ROGUE})

    # The header is either absent or names someone else — never the caller.
    assert response.headers.get("access-control-allow-origin") != ROGUE


def test_a_rogue_origin_preflight_is_not_granted(client):
    response = client.options(
        "/scrape",
        headers={
            "Origin": ROGUE,
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.headers.get("access-control-allow-origin") != ROGUE


def test_a_configured_origin_is_allowed(client):
    from src.config import config

    allowed = config.allowed_origin_list[0]

    response = client.get("/health", headers={"Origin": allowed})

    assert response.headers.get("access-control-allow-origin") == allowed


def test_scrape_of_a_blocked_url_returns_400_not_500(client):
    response = client.post(
        "/scrape", json={"url": "http://169.254.169.254/latest/meta-data/"}
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "169.254.169.254" not in detail  # no internal detail echoed back


def test_scrape_of_a_blocked_url_names_the_reason(client):
    response = client.post("/scrape", json={"url": "https://evil.example.com/x"})

    assert response.status_code == 400
    assert "permitid" in response.json()["detail"].lower()
