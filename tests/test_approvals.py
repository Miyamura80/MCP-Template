"""Approval of dangerous tools: the shared gate, MCP, REST and the dashboard."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from db.models.approvals import PendingAction
from mcp_server._tool_factory import make_tool
from mcp_server.server import mcp
from models.approvals import ApprovalSettings, stricter
from services import _registry, service
from services.approvals_svc import (
    ApprovalDeniedError,
    ApprovalRequiredError,
    BrowserApprovalRequiredError,
    approve_and_execute,
    check_gate,
    deny,
    effective_mode,
    get_approval_view,
    save_settings,
)
from tests.test_mcp_e2e import _patch_db
from tests.test_template import TestTemplate

_TOOL = "test_danger_echo"
_calls: list[str] = []


class _EchoIn(BaseModel):
    user_id: str = ""
    text: str


class _EchoOut(BaseModel):
    text: str
    run: int


@contextmanager
def _danger_tool():
    """Register a dangerous service on the registry and the MCP server."""

    @service(
        name=_TOOL,
        description="Echo, dangerously.",
        input_model=_EchoIn,
        output_model=_EchoOut,
        risk="dangerous",
    )
    def _echo(inp: _EchoIn) -> _EchoOut:
        _calls.append(inp.text)
        return _EchoOut(text=inp.text, run=len(_calls))

    entry = next(e for e in _registry if e.name == _TOOL)
    assert entry.func is _echo
    make_tool(mcp, entry)
    _calls.clear()
    try:
        yield entry
    finally:
        _registry[:] = [e for e in _registry if e.name != _TOOL]
        mcp._tool_manager._tools.pop(_TOOL, None)


def _set_mode(user: str, mode: str, **overrides: str) -> None:
    save_settings(
        user,
        ApprovalSettings.model_validate({"mode": mode, "overrides": overrides}),
    )


def _held(entry, user: str, text: str = "hi", url_mode=None, inline=False):
    with pytest.raises(ApprovalRequiredError) as info:
        check_gate(
            entry,
            _EchoIn(user_id=user, text=text),
            user_id=user,
            url_mode=url_mode,
            can_confirm_inline=inline,
        )
    return info.value.view


class TestModes(TestTemplate):
    def test_stricter(self):
        assert stricter("automatic", None) == "automatic"
        assert stricter("agent", "automatic") == "agent"
        assert stricter("agent", "browser") == "browser"

    def test_url_cannot_loosen(self):
        with _patch_db(), _danger_tool():
            _set_mode("u", "browser")
            assert effective_mode("u", _TOOL, "automatic") == "browser"

    def test_override_beats_default(self):
        with _patch_db(), _danger_tool():
            _set_mode("u", "automatic", **{_TOOL: "agent"})
            assert effective_mode("u", _TOOL, None) == "agent"

    def test_overrides_only_for_dangerous_tools(self):
        with _patch_db(), _danger_tool(), pytest.raises(ValueError, match="dangerous"):
            _set_mode("u", "agent", gmail_compose="browser")


class TestGate(TestTemplate):
    def test_automatic_runs(self):
        with _patch_db(), _danger_tool() as entry:
            d = check_gate(
                entry,
                _EchoIn(text="x"),
                user_id="u",
                url_mode=None,
                can_confirm_inline=False,
            )
            assert d.action_id is None and d.replay is None

    def test_unauthenticated_and_safe_tools_skip(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "browser")
            assert check_gate(
                entry, _EchoIn(text="x"), user_id=None, url_mode=None,
                can_confirm_inline=False,
            ).action_id is None  # fmt: skip

    def test_agent_confirm_runs_stored_args_once(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "agent")
            view = _held(entry, "u", "stored")
            assert view.mode == "agent" and view.approve_url is None
            done = approve_and_execute("u", view.id, modes=frozenset({"agent"}))
            assert done.status == "executed"
            assert done.result == {"text": "stored", "run": 1}
            # Single use: a second confirm does not run it again.
            again = approve_and_execute("u", view.id, modes=frozenset({"agent"}))
            assert again.status == "executed" and _calls == ["stored"]

    def test_retry_replays_instead_of_rerunning(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "agent")
            view = _held(entry, "u")
            assert _held(entry, "u").id == view.id  # no second approval
            approve_and_execute("u", view.id, modes=frozenset({"agent"}))
            d = check_gate(
                entry,
                _EchoIn(user_id="u", text="hi"),
                user_id="u",
                url_mode=None,
                can_confirm_inline=False,
            )
            assert d.replay == {"text": "hi", "run": 1}
            assert _calls == ["hi"]

    def test_other_args_are_a_new_request(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "agent")
            assert _held(entry, "u", "a").id != _held(entry, "u", "b").id

    def test_agent_cannot_confirm_browser_mode(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "browser")
            view = _held(entry, "u")
            with pytest.raises(ValueError, match="only the user"):
                approve_and_execute("u", view.id, modes=frozenset({"agent"}))
            assert _calls == []

    def test_bound_to_user(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "agent")
            view = _held(entry, "u")
            with pytest.raises(ValueError, match="No approval"):
                approve_and_execute("mallory", view.id, modes=frozenset({"agent"}))

    def test_elicitation_escalates_without_inline_channel(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "elicitation")
            assert _held(entry, "u", inline=False).mode == "browser"
            assert _held(entry, "u", "y", inline=True).mode == "elicitation"

    def test_browser_is_a_connect_flow_with_link(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "browser")
            with pytest.raises(BrowserApprovalRequiredError) as info:
                check_gate(
                    entry,
                    _EchoIn(text="x"),
                    user_id="u",
                    url_mode=None,
                    can_confirm_inline=False,
                )
            url = info.value.build_auth_url()
            assert url and url.endswith(f"/dashboard/approvals/{info.value.view.id}")

    def test_denied_stays_denied(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "browser")
            view = _held(entry, "u")
            assert deny("u", view.id).status == "denied"
            with pytest.raises(ApprovalDeniedError):
                check_gate(
                    entry,
                    _EchoIn(user_id="u", text="hi"),
                    user_id="u",
                    url_mode=None,
                    can_confirm_inline=False,
                )

    def test_expiry(self):
        with _patch_db() as factory, _danger_tool() as entry:
            _set_mode("u", "agent")
            view = _held(entry, "u")
            with factory() as s:
                s.query(PendingAction).filter_by(id=view.id).update(
                    {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
                )
                s.commit()
            assert get_approval_view("u", view.id).status == "expired"
            done = approve_and_execute("u", view.id, modes=frozenset({"agent"}))
            assert done.status == "expired" and _calls == []
            assert _held(entry, "u").id != view.id  # a fresh request

    def test_failure_is_recorded(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "agent")
            view = _held(entry, "u")
            with patch.object(entry, "func", side_effect=RuntimeError("boom")):
                done = approve_and_execute("u", view.id, modes=frozenset({"agent"}))
            assert done.status == "failed" and "boom" not in (done.error or "")

    def test_dangerous_cannot_be_priced(self):
        with pytest.raises(ValueError, match="priced"):
            service(
                name="x",
                description="",
                input_model=_EchoIn,
                output_model=_EchoOut,
                risk="dangerous",
                price="0.01",
            )
