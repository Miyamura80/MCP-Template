"""Approval over the wire: MCP (agent, browser, elicitation), REST and the dashboard."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api_server.server import app
from common import global_config
from services import _registry
from services.approvals_svc import (
    ApprovalRequiredError,
    BrowserApprovalRequiredError,
    approve_and_execute,
    check_gate,
    effective_mode,
)
from src.utils.current_user import reset_current_user, set_current_user
from tests.test_approvals import _TOOL, _calls, _danger_tool, _EchoIn, _set_mode
from tests.test_mcp_e2e import _McpSession, _patch_db, _wire_client
from tests.test_mcp_protocol_eras import _ModernMcpClient
from tests.test_template import TestTemplate


def _tool_text(result: dict) -> str:
    return result["content"][0]["text"]


class TestMcp(TestTemplate):
    def test_agent_mode_end_to_end(self):
        with _wire_client("u-mcp") as (client, key), _danger_tool():
            _set_mode("u-mcp", "agent")
            session = _McpSession(client, key)
            session.handshake()
            args = {"name": _TOOL, "arguments": {"text": "send it"}}
            held = session.request("tools/call", args)
            assert held["isError"] is True and "confirm_action" in _tool_text(held)
            approval_id = _tool_text(held).split("approval_id='")[1].split("'")[0]
            done = session.request(
                "tools/call",
                {"name": "confirm_action", "arguments": {"approval_id": approval_id}},
            )
            assert done["structuredContent"]["status"] == "executed"
            # Retrying the original call replays the result.
            again = session.request("tools/call", args)
            assert again["structuredContent"] == {"text": "send it", "run": 1}
            assert _calls == ["send it"]

    def test_url_flag_tightens(self):
        with _wire_client("u-url") as (client, key), _danger_tool():
            session = _McpSession(client, key, path="/mcp?approval=agent")
            session.handshake()
            held = session.request(
                "tools/call", {"name": _TOOL, "arguments": {"text": "x"}}
            )
            assert held["isError"] is True and _calls == []

    def test_browser_mode_uses_url_elicitation(self):
        with _wire_client("u-br") as (client, key), _danger_tool():
            _set_mode("u-br", "browser")
            session = _McpSession(client, key)
            session.handshake()
            error = session.request_error(
                "tools/call", {"name": _TOOL, "arguments": {"text": "x"}}
            )
            assert error["code"] == -32042
            assert (
                "/dashboard/approvals/apr_" in error["data"]["elicitations"][0]["url"]
            )

    def test_hidden_with_dangerous_tools_false(self):
        with _wire_client("u-h") as (client, key), _danger_tool():
            session = _McpSession(client, key, path="/mcp?dangerous_tools=false")
            session.handshake()
            names = {t["name"] for t in session.request("tools/list")["tools"]}
            assert names.isdisjoint({_TOOL, "confirm_action", "get_approval"})

    def _modern(self, client, key):
        return _ModernMcpClient(client, key, capabilities={"elicitation": {}})

    def test_elicitation_mrtr_accept(self):
        with _wire_client("u-el") as (client, key), _danger_tool():
            _set_mode("u-el", "elicitation")
            modern = self._modern(client, key)
            params = {"name": _TOOL, "arguments": {"text": "ok?"}}
            first = modern.request("tools/call", params)
            assert first["resultType"] == "input_required"
            assert "ok?" in first["inputRequests"]["approval"]["params"]["message"]
            assert _calls == []
            second = modern.request(
                "tools/call",
                {
                    **params,
                    "requestState": first["requestState"],
                    "inputResponses": {
                        "approval": {"action": "accept", "content": {"confirm": True}}
                    },
                },
            )
            assert second["structuredContent"] == {"text": "ok?", "run": 1}

    def test_elicitation_mrtr_decline(self):
        with _wire_client("u-el2") as (client, key), _danger_tool():
            _set_mode("u-el2", "elicitation")
            modern = self._modern(client, key)
            params = {"name": _TOOL, "arguments": {"text": "no"}}
            first = modern.request("tools/call", params)
            second = modern.request(
                "tools/call",
                {
                    **params,
                    "requestState": first["requestState"],
                    "inputResponses": {"approval": {"action": "decline"}},
                },
            )
            assert second["isError"] is True and "denied" in _tool_text(second)
            assert _calls == []

    def test_modern_without_elicitation_escalates_to_browser(self):
        with _wire_client("u-el3") as (client, key), _danger_tool():
            _set_mode("u-el3", "elicitation")
            modern = _ModernMcpClient(client, key, capabilities={})
            resp, msg = modern.post(
                "tools/call", {"name": _TOOL, "arguments": {"text": "x"}}
            )
            assert "elicitations" in str(msg) or "dashboard" in str(msg)
            assert _calls == []


class TestRest(TestTemplate):
    def _post(self, client, key, idem):
        return client.post(
            "/api/v1/services/gmail_disconnect",
            json={},
            headers={"X-API-KEY": key, "Idempotency-Key": idem},
        )

    def test_202_then_replay(self):
        from models.gmail import GmailDisconnectResult  # noqa: PLC0415

        with _wire_client("u-rest") as (client, key):
            _set_mode("u-rest", "agent")
            resp = self._post(client, key, "k1")
            assert resp.status_code == 202, resp.text
            body = resp.json()
            assert body["status"] == "approval_required"
            assert "confirm_action" in body["message"]
            entry = next(e for e in _registry if e.name == "gmail_disconnect")
            fake = GmailDisconnectResult(revoked=True)
            with patch.object(entry, "func", return_value=fake):
                approve_and_execute(
                    "u-rest", body["approval"]["id"], modes=frozenset({"agent"})
                )
            replay = self._post(client, key, "k2")
            assert replay.status_code == 200, replay.text
            assert replay.json() == {"revoked": True}


class TestDashboard(TestTemplate):
    @contextmanager
    def _client(self):
        with (
            _patch_db(),
            _danger_tool(),
            patch.object(global_config, "WORKOS_CLIENT_ID", None),
            patch.object(global_config, "WORKOS_API_KEY", None),
            TestClient(app) as client,
        ):
            yield client

    def _login(self, client, user="human"):
        resp = client.post(
            "/dashboard/login",
            data={"user_id": user, "next": "/dashboard/approvals"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        return client.get("/api/v1/dashboard/session").json()["csrf_token"]

    def test_requires_session(self):
        with self._client() as client:
            assert client.get("/api/v1/approvals").status_code == 401
            page = client.get("/dashboard/approvals", follow_redirects=False)
            assert (
                page.status_code == 303
                and "/dashboard/login" in page.headers["location"]
            )

    def test_api_key_is_not_a_session(self):
        with self._client() as client:
            resp = client.get("/api/v1/approvals", headers={"X-API-KEY": "sk_x"})
            assert resp.status_code == 401

    def test_dev_login_refused_in_prod(self):
        with self._client() as client, patch.object(global_config, "DEV_ENV", "prod"):
            resp = client.post("/dashboard/login", data={"user_id": "x"})
            assert resp.status_code == 403
            assert client.get("/dashboard/login").status_code == 503

    def test_approve_from_dashboard(self):
        with self._client() as client:
            csrf = self._login(client)
            _set_mode("human", "browser")
            with set_user("human"), pytest.raises(BrowserApprovalRequiredError) as info:
                check_gate(
                    next(e for e in _registry if e.name == _TOOL),
                    _EchoIn(user_id="human", text="from page"),
                    user_id="human",
                    url_mode=None,
                    can_confirm_inline=False,
                )
            approval_id = info.value.view.id
            page = client.get(f"/dashboard/approvals/{approval_id}")
            assert "from page" in page.text and "Approve and run" in page.text
            # Missing CSRF is refused.
            bad = client.post(f"/api/v1/approvals/{approval_id}/approve")
            assert bad.status_code == 403 and _calls == []
            ok = client.post(
                f"/api/v1/approvals/{approval_id}/approve",
                headers={"X-CSRF-Token": csrf},
            )
            assert ok.status_code == 200 and ok.json()["status"] == "executed"
            assert _calls == ["from page"]

    def test_other_users_approval_is_invisible(self):
        with self._client() as client:
            csrf = self._login(client, "eve")
            _set_mode("victim", "browser")
            with pytest.raises(ApprovalRequiredError) as info:
                check_gate(
                    next(e for e in _registry if e.name == _TOOL),
                    _EchoIn(user_id="victim", text="x"),
                    user_id="victim",
                    url_mode=None,
                    can_confirm_inline=False,
                )
            approval_id = info.value.view.id
            assert client.get(f"/dashboard/approvals/{approval_id}").status_code == 404
            resp = client.post(
                f"/api/v1/approvals/{approval_id}/approve",
                headers={"X-CSRF-Token": csrf},
            )
            assert resp.status_code == 400 and _calls == []

    def test_settings_round_trip(self):
        with self._client() as client:
            csrf = self._login(client)
            resp = client.put(
                "/api/v1/settings/approval",
                json={"mode": "agent", "overrides": {_TOOL: "browser"}},
                headers={"X-CSRF-Token": csrf},
            )
            assert resp.status_code == 200, resp.text
            assert effective_mode("human", _TOOL, None) == "browser"
            form = client.post(
                "/dashboard/approval",
                data={"csrf": csrf, "mode": "elicitation", f"tool:{_TOOL}": ""},
                follow_redirects=False,
            )
            assert form.status_code == 303
            assert effective_mode("human", _TOOL, None) == "elicitation"

    def test_open_redirect_blocked(self):
        with self._client() as client:
            resp = client.post(
                "/dashboard/login",
                data={"user_id": "x", "next": "https://evil.example/"},
                follow_redirects=False,
            )
            assert resp.headers["location"] == "/dashboard/approvals"


@contextmanager
def set_user(user_id: str):
    from api_server.auth.unified_auth import AuthenticatedUser  # noqa: PLC0415

    token = set_current_user(
        AuthenticatedUser(user_id=user_id, auth_method="api_key", scopes=["*"])
    )
    try:
        yield
    finally:
        reset_current_user(token)
