"""Wire-level tests for the no-auth /mcp-demo mount.

Runs full MCP sessions against the demo mount with NO credentials and asserts
the gate (enabled flag, rate limit), the tool surface, wire-format parity with
production (outputSchema, ``_meta.ui``), and the simulated-mutation semantics.
Complements ``tests/test_mcp_e2e.py``, which covers the authenticated /mcp
mount the demo mirrors.
"""

import json
from contextlib import contextmanager

from fastapi.testclient import TestClient

from api_server.server import app
from common import global_config
from mcp_server.demo import fixtures
from mcp_server.demo.server import demo_mcp, reset_rate_limiter
from tests.test_template import TestTemplate

_PROTOCOL_VERSION = "2025-03-26"
# Satisfies the DNS-rebinding allowlist (loopback), same as test_mcp_e2e.
_HOST = "127.0.0.1:8080"


def _read_sse_first_message(resp) -> dict:
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :])
    raise AssertionError(f"no SSE data line in response: {resp.text!r}")


class _DemoSession:
    """Minimal MCP client over /mcp-demo - deliberately sends no credentials."""

    def __init__(self, client: TestClient) -> None:
        self._client = client
        self._next_id = 0
        self._protocol_version = _PROTOCOL_VERSION

    def _headers(self) -> dict:
        return {
            "Accept": "application/json, text/event-stream",
            "Host": _HOST,
            "MCP-Protocol-Version": self._protocol_version,
        }

    def request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        resp = self._client.post(
            "/mcp-demo",
            headers=self._headers(),
            json={
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": method,
                "params": params or {},
            },
        )
        assert resp.status_code == 200, f"{method}: {resp.status_code} {resp.text}"
        msg = _read_sse_first_message(resp)
        assert "error" not in msg, f"{method} returned error: {msg.get('error')}"
        return msg["result"]

    def call(self, name: str, arguments: dict | None = None) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def handshake(self) -> dict:
        result = self.request(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "demo-test", "version": "0"},
            },
        )
        self._protocol_version = result["protocolVersion"]
        resp = self._client.post(
            "/mcp-demo",
            headers=self._headers(),
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        assert resp.status_code == 202
        return result


@contextmanager
def _demo_session(rate_limit_per_minute: int | None = None):
    original_enabled = global_config.demo.enabled
    original_rate = global_config.demo.rate_limit_per_minute
    global_config.demo.enabled = True
    if rate_limit_per_minute is not None:
        global_config.demo.rate_limit_per_minute = rate_limit_per_minute
    # Every test shares the TestClient IP; clear the window so earlier tests'
    # requests don't count against this one's budget.
    reset_rate_limiter()
    try:
        with TestClient(app) as client:
            session = _DemoSession(client)
            session.handshake()
            yield session
    finally:
        global_config.demo.enabled = original_enabled
        global_config.demo.rate_limit_per_minute = original_rate


class TestDemoGate(TestTemplate):
    def test_disabled_answers_404(self):
        original = global_config.demo.enabled
        global_config.demo.enabled = False
        try:
            with TestClient(app) as client:
                resp = client.post(
                    "/mcp-demo",
                    headers={"Host": _HOST},
                    json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                )
                assert resp.status_code == 404
        finally:
            global_config.demo.enabled = original

    def test_no_credentials_required(self):
        with _demo_session() as session:
            result = session.request("tools/list")
            assert result["tools"]

    def test_main_mcp_still_requires_auth(self):
        # The demo path exemption must not loosen the real mount: an
        # unauthenticated /mcp initialize stays 401.
        with _demo_session() as session:
            resp = session._client.post(
                "/mcp",
                headers=session._headers(),
                json={"jsonrpc": "2.0", "id": 99, "method": "initialize"},
            )
            assert resp.status_code == 401

    def test_rate_limit_answers_429(self):
        with _demo_session(rate_limit_per_minute=3) as session:
            # The handshake burned 2 requests (initialize + initialized).
            session.request("tools/list")
            resp = session._client.post(
                "/mcp-demo",
                headers=session._headers(),
                json={"jsonrpc": "2.0", "id": 50, "method": "tools/list"},
            )
            assert resp.status_code == 429
            assert resp.headers["retry-after"] == "60"


class TestDemoToolSurface(TestTemplate):
    def test_llm_tools_publish_schema_and_app_meta(self):
        with _demo_session() as session:
            tools = {t["name"]: t for t in session.request("tools/list")["tools"]}

            for name in (
                "gmail_curate_inbox",
                "gmail_get_thread",
                "gmail_compose",
                "gmail_reply_to_thread",
            ):
                tool = tools[name]
                assert tool["outputSchema"]["type"] == "object", name
                assert tool["_meta"]["ui"]["resourceUri"] == "ui://mymcp/gmail_inbox", (
                    name
                )

            # App-only tools are hinted hidden from the LLM.
            assert tools["gmail_inbox.open_thread"]["_meta"]["ui"]["visibility"] == [
                "app"
            ]
            assert tools["gmail_composer.send"]["_meta"]["ui"]["visibility"] == ["app"]

    def test_surface_has_no_real_mailbox_tools(self):
        # The demo must never expose account-linking or webhook tools - there
        # is no real account to link.
        with _demo_session() as session:
            names = {t["name"] for t in session.request("tools/list")["tools"]}
            assert not any(n.startswith("gmail_connect") for n in names)
            assert not any("webhook" in n for n in names)

    def test_app_resources_served(self):
        with _demo_session() as session:
            uris = {r["uri"] for r in session.request("resources/list")["resources"]}
            assert uris == {"ui://mymcp/gmail_inbox", "ui://mymcp/gmail_composer"}


class TestDemoToolBehavior(TestTemplate):
    def test_curate_inbox_returns_fixtures_with_app_meta(self):
        with _demo_session() as session:
            result = session.call("gmail_curate_inbox", {"limit": 2})
            threads = result["structuredContent"]["threads"]
            assert len(threads) == 2
            assert threads[0]["thread_id"] == "t-1001"
            assert threads[0]["importance_score"] == 0.97
            # Same dual-keyed app meta shape as the production enhanced tools.
            assert result["_meta"]["ui"]["resourceUri"] == "ui://mymcp/gmail_inbox"
            assert result["_meta"]["ui/resourceUri"] == "ui://mymcp/gmail_inbox"

    def test_get_thread_unknown_id_is_friendly_error(self):
        with _demo_session() as session:
            result = session.call("gmail_get_thread", {"thread_id": "nope"})
            assert result["isError"] is True
            assert "t-1001" in result["content"][0]["text"]

    def test_reply_derives_recipient_and_subject_from_thread(self):
        with _demo_session() as session:
            result = session.call(
                "gmail_reply_to_thread",
                {"thread_id": "t-1002", "body": "Sounds good"},
            )
            draft = result["structuredContent"]
            assert draft["to"] == "Priya Nair <priya@peoplehq.io>"
            assert draft["subject"] == "Re: Onsite interview loop for Staff Eng"
            assert draft["thread_id"] == "t-1002"

    def test_composer_roundtrip_save_then_send(self):
        with _demo_session() as session:
            draft = session.call(
                "gmail_compose",
                {"to": "a@b.c", "subject": "Hi", "body": "first"},
            )["structuredContent"]

            saved = session.call(
                "gmail_composer.save_draft",
                {"draft_id": draft["draft_id"], "body": "edited"},
            )["structuredContent"]
            assert saved["body"] == "edited"
            assert saved["to"] == "a@b.c"  # omitted field preserved (UNSET)

            sent = session.call("gmail_composer.send", {"draft_id": draft["draft_id"]})[
                "structuredContent"
            ]
            assert sent["message_id"].startswith("demo-sent-")

    def test_mutations_do_not_change_the_fixture_inbox(self):
        with _demo_session() as session:
            session.call("gmail_inbox.archive", {"thread_id": "t-1003"})
            result = session.call("gmail_curate_inbox", {})
            ids = [t["thread_id"] for t in result["structuredContent"]["threads"]]
            assert "t-1003" in ids  # archive is simulated, nothing persists

    def test_fixture_models_are_valid_service_outputs(self):
        # Fixtures must stay importable as the production output models so
        # wire parity holds; a schema drift shows up here, not in production.
        inbox = fixtures.curated_inbox()
        assert inbox.threads[0].labels[0].name == "Unread"
        thread = fixtures.get_thread("t-1002")
        assert thread.draft is not None
        assert thread.draft.draft_id == "d-9001"

    def test_demo_server_instructions_mention_demo(self):
        assert "DEMO" in (demo_mcp.instructions or "")
