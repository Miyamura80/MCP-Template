"""Tests for the SEP-2127 MCP Server Card served at /.well-known/mcp/.

The card is pre-connect branding, so unlike the OAuth metadata it must be
available regardless of auth config and readable cross-origin.
"""

from fastapi.testclient import TestClient

from api_server.routes import well_known
from api_server.server import app

CARD_PATH = "/.well-known/mcp/server-card.json"


class TestServerCard:
    def _client(self) -> TestClient:
        # No lifespan needed: this route never reaches the MCP sub-app.
        return TestClient(app)

    def test_card_served_with_branding(self):
        resp = self._client().get(CARD_PATH)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")

        body = resp.json()
        # The three fields the registry audit checks for: name, description, icon.
        assert body["name"] == "io.github.Miyamura80/MCP-Template"
        assert body["title"]
        assert body["description"]
        assert body["icons"] and body["icons"][0]["src"].startswith("https://")
        # No $schema: the draft SEP-2127 server-card schema URL is unpublished (404).
        assert "$schema" not in body

    def test_card_omits_remote_without_public_url(self, monkeypatch):
        # MCP_PUBLIC_URL unset (e.g. deployed no-OAuth server): the card must not
        # advertise the localhost fallback as a discoverable endpoint.
        monkeypatch.setattr(well_known.global_config, "MCP_PUBLIC_URL", None)
        body = self._client().get(CARD_PATH).json()
        assert "localhost" not in str(body)
        assert not body.get("remotes")

    def test_card_advertises_public_remote_when_configured(self, monkeypatch):
        url = "https://mcp.example.com/mcp"
        monkeypatch.setattr(well_known.global_config, "MCP_PUBLIC_URL", url)
        body = self._client().get(CARD_PATH).json()
        remotes = body["remotes"]
        assert remotes and remotes[0] == {"type": "streamable-http", "url": url}

    def test_card_is_cors_readable(self):
        # Registry crawlers fetch the card cross-origin; it must allow that.
        resp = self._client().get(CARD_PATH)
        assert resp.headers.get("access-control-allow-origin") == "*"
