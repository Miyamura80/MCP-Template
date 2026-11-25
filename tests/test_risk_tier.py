"""Risk tiers on ``@service``, the MCP annotations they set, and MCP URL flags."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from api_server.auth.authkit_auth import audience_matches, strip_resource_flags
from api_server.oauth.storage import resource_matches
from mcp_server._tool_factory import tool_annotations
from mcp_server.server import llm_tool_surface
from services import ServiceEntry, _resolve_risk
from src.utils.mcp_url_flags import (
    DEFAULT_FLAGS,
    InvalidUrlFlagError,
    McpUrlFlags,
    parse_url_flags,
)
from tests.test_mcp_e2e import _McpSession, _wire_client
from tests.test_template import TestTemplate

# Pinned so a service can't quietly move in or out of the gated tier.
_DANGEROUS = {
    "gmail_send",
    "gmail_discard_draft",
    "gmail_disconnect",
    "webhook_unsubscribe",
}


def _entry(risk: str) -> ServiceEntry:
    return ServiceEntry(
        name="x",
        description="",
        input_model=object,
        output_model=object,
        func=lambda _: None,
        risk=risk,  # ty: ignore[invalid-argument-type]
    )


class TestResolveRisk(TestTemplate):
    def test_defaults_follow_mutating(self):
        assert _resolve_risk("s", False, None) == (False, "read")
        assert _resolve_risk("s", True, None) == (True, "write")

    def test_dangerous_implies_mutating(self):
        assert _resolve_risk("s", False, "dangerous") == (True, "dangerous")

    def test_mutating_read_is_rejected(self):
        with pytest.raises(ValueError, match="cannot be 'read'"):
            _resolve_risk("s", True, "read")

    def test_unknown_risk_rejected(self):
        with pytest.raises(ValueError, match="risk must be one of"):
            _resolve_risk("s", False, "spicy")  # ty: ignore[invalid-argument-type]


class TestRegistry(TestTemplate):
    def test_dangerous_set_pinned(self):
        assert {
            e.name for e in llm_tool_surface() if e.risk == "dangerous"
        } == _DANGEROUS

    def test_dangerous_are_mutating(self):
        assert all(e.mutating for e in llm_tool_surface() if e.risk != "read")


class TestAnnotations(TestTemplate):
    def test_read(self):
        a = tool_annotations(_entry("read"))
        assert a.read_only_hint is True and a.destructive_hint is None

    def test_write_is_explicitly_not_destructive(self):
        a = tool_annotations(_entry("write"))
        assert a.read_only_hint is False and a.destructive_hint is False

    def test_dangerous(self):
        a = tool_annotations(_entry("dangerous"))
        assert a.read_only_hint is False and a.destructive_hint is True


class TestParseFlags(TestTemplate):
    def test_empty_is_default(self):
        assert parse_url_flags(b"") == DEFAULT_FLAGS

    def test_values(self):
        assert parse_url_flags("dangerous_tools=FALSE&approval=browser") == McpUrlFlags(
            dangerous_tools=False, approval="browser"
        )
        assert parse_url_flags("dangerous_tools=1").dangerous_tools is True

    def test_unknown_params_ignored(self):
        assert parse_url_flags("utm_source=x") == DEFAULT_FLAGS

    @pytest.mark.parametrize(
        "query",
        [
            "dangerous_tools=maybe",
            "approval=sometimes",
            "approval=agent&approval=browser",
        ],
    )
    def test_invalid(self, query):
        with pytest.raises(InvalidUrlFlagError):
            parse_url_flags(query)


class TestAudienceIgnoresFlags(TestTemplate):
    def test_strip(self):
        assert (
            strip_resource_flags("https://h/mcp/?dangerous_tools=false#x")
            == "https://h/mcp"
        )

    @patch("api_server.oauth.storage.mcp_resource_url", return_value="https://h/mcp")
    @patch(
        "api_server.auth.authkit_auth.mcp_resource_url", return_value="https://h/mcp"
    )
    def test_matches(self, _ak, _st):
        assert resource_matches("https://h/mcp?approval=browser")
        assert audience_matches(["other", "https://h/mcp?dangerous_tools=false"])
        assert not resource_matches("https://h/other?x=1")
        assert not audience_matches("https://evil/mcp")
        assert not audience_matches(None)


class TestWire(TestTemplate):
    def _tools(self, path: str) -> dict[str, dict]:
        with _wire_client("u-risk") as (client, key):
            session = _McpSession(client, key, path=path)
            session.handshake()
            return {t["name"]: t for t in session.request("tools/list")["tools"]}

    def test_annotations_on_the_wire(self):
        tools = self._tools("/mcp")
        assert tools["gmail_send"]["annotations"]["destructiveHint"] is True
        assert tools["gmail_compose"]["annotations"]["destructiveHint"] is False
        assert tools["gmail_list_inbox"]["annotations"]["readOnlyHint"] is True

    def test_dangerous_tools_false_hides_and_refuses(self):
        path = "/mcp?dangerous_tools=false"
        with _wire_client("u-risk") as (client, key):
            session = _McpSession(client, key, path=path)
            session.handshake()
            names = {t["name"] for t in session.request("tools/list")["tools"]}
            assert names.isdisjoint(_DANGEROUS)
            assert "gmail_compose" in names
            result = session.request(
                "tools/call", {"name": "gmail_send", "arguments": {"draft_id": "d"}}
            )
        assert result["isError"] is True
        assert "dangerous_tools=false" in result["content"][0]["text"]

    def test_invalid_flag_is_400(self):
        with _wire_client("u-risk") as (client, key):
            resp = client.post(
                "/mcp?approval=yolo",
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Host": "127.0.0.1:8080",
                    "X-API-KEY": key,
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
        assert resp.status_code == 400
        assert "automatic" in resp.json()["error"]["message"]
