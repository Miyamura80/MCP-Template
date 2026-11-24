"""Approval of dangerous tools: the shared gate (``services/approvals_svc.py``)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel

from db.models.approvals import PendingAction
from mcp_server._tool_factory import make_tool
from mcp_server.server import mcp
from models.approvals import ApprovalIdInput, ApprovalSettings, stricter
from services import _registry, service
from services.approvals_errors import (
    ApprovalBusyError,
    ApprovalDeniedError,
    ApprovalRequiredError,
    BrowserApprovalRequiredError,
)
from services.approvals_svc import (
    Proceed,
    Replay,
    RunHeld,
    approve_as_human,
    check_gate,
    confirm_action,
    deny,
    effective_mode,
    get_approval_view,
    running,
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


def _gate(entry, user: str | None, text: str = "hi", url_mode=None, inline=False):
    return check_gate(
        entry,
        _EchoIn(user_id=user or "", text=text),
        user_id=user,
        url_mode=url_mode,
        can_confirm_inline=inline,
    )


def _held(entry, user: str, text: str = "hi", url_mode=None, inline=False):
    with pytest.raises(ApprovalRequiredError) as info:
        _gate(entry, user, text, url_mode, inline)
    return info.value.view


def _run(entry, user: str, text: str = "hi", url_mode=None):
    """What a transport does: gate, then run inside ``running``."""
    decision = _gate(entry, user, text, url_mode)
    if isinstance(decision, Replay):
        return decision.output
    with running(decision) as done:
        result = entry.func(_EchoIn(user_id=user, text=text))
        done(result)
    return result.model_dump()


def _confirm(user: str, approval_id: str):
    return confirm_action(ApprovalIdInput(user_id=user, approval_id=approval_id))


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
            assert isinstance(_gate(entry, "u"), Proceed)

    def test_local_transport_is_never_gated(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "browser")
            assert isinstance(_gate(entry, None), Proceed)

    def test_empty_user_is_refused(self):
        with _patch_db(), _danger_tool() as entry, pytest.raises(ValueError):
            _gate(entry, "")

    def test_confirm_does_not_run_retry_does_once(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "agent")
            view = _held(entry, "u", "stored")
            assert view.mode == "agent" and view.approve_url is None
            assert _confirm("u", view.id).status == "approved"
            assert _calls == []  # approving runs nothing
            assert isinstance(_gate(entry, "u", "stored"), RunHeld)
            assert _run(entry, "u", "stored") == {"text": "stored", "run": 1}
            # Later retries replay; nothing runs twice.
            assert _run(entry, "u", "stored") == {"text": "stored", "run": 1}
            assert _calls == ["stored"]
            assert get_approval_view("u", view.id).status == "executed"

    def test_retry_while_pending_reuses_the_approval(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "agent")
            assert _held(entry, "u").id == _held(entry, "u").id

    def test_other_args_are_a_new_request(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "agent")
            assert _held(entry, "u", "a").id != _held(entry, "u", "b").id

    def test_agent_cannot_confirm_browser_mode(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "browser")
            view = _held(entry, "u")
            with pytest.raises(ValueError, match="needs the user"):
                _confirm("u", view.id)

    def test_tightened_floor_revokes_agent_confirm(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "agent")
            view = _held(entry, "u")
            _set_mode("u", "browser")
            with pytest.raises(ValueError, match="needs the user"):
                _confirm("u", view.id)

    def test_stricter_url_escalates_an_agent_approval(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "agent")
            view = _held(entry, "u")
            _confirm("u", view.id)
            escalated = _held(entry, "u", url_mode="browser")
            assert escalated.id == view.id
            assert (escalated.mode, escalated.status) == ("browser", "pending")
            assert _calls == []

    def test_bound_to_user(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "agent")
            view = _held(entry, "u")
            with pytest.raises(ValueError, match="No approval"):
                _confirm("mallory", view.id)
            with pytest.raises(ValueError, match="No approval"):
                approve_as_human("mallory", view.id)

    def test_elicitation_escalates_without_inline_channel(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "elicitation")
            assert _held(entry, "u", inline=False).mode == "browser"
            assert _held(entry, "u", "y", inline=True).mode == "elicitation"

    def test_browser_hold_carries_its_link(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "browser")
            with pytest.raises(BrowserApprovalRequiredError) as info:
                _gate(entry, "u")
            url = info.value.build_auth_url()
            assert url and url.endswith(f"/dashboard/approvals/{info.value.view.id}")

    def test_denied_stays_denied(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "browser")
            view = _held(entry, "u")
            assert deny("u", view.id).status == "denied"
            with pytest.raises(ApprovalDeniedError):
                _gate(entry, "u")

    def test_expired_cannot_be_approved(self):
        with _patch_db() as factory, _danger_tool() as entry:
            _set_mode("u", "agent")
            view = _held(entry, "u")
            with factory() as s:
                s.query(PendingAction).filter_by(id=view.id).update(
                    {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
                )
                s.commit()
            assert get_approval_view("u", view.id).status == "expired"
            assert _confirm("u", view.id).status == "expired"
            fresh = _held(entry, "u")
            assert fresh.id != view.id and _calls == []

    def test_failure_is_recorded_not_left_executing(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "agent")
            view = _held(entry, "u")
            _confirm("u", view.id)
            decision = _gate(entry, "u")
            with pytest.raises(RuntimeError), running(decision):
                raise RuntimeError("boom")
            done = get_approval_view("u", view.id)
            assert done.status == "failed" and "boom" not in (done.error or "")

    def test_a_claimed_call_cannot_run_twice(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "agent")
            _confirm("u", _held(entry, "u").id)
            first, second = _gate(entry, "u"), _gate(entry, "u")
            with running(first):
                with pytest.raises(ApprovalBusyError), running(second):
                    pass
                with pytest.raises(ApprovalBusyError):
                    _gate(entry, "u")  # executing: a retry waits, never reruns

    def test_crashed_run_becomes_unknown_not_busy_forever(self):
        with _patch_db() as factory, _danger_tool() as entry:
            _set_mode("u", "agent")
            view = _held(entry, "u")
            _confirm("u", view.id)
            with factory() as s:  # the process died while it was executing
                s.query(PendingAction).filter_by(id=view.id).update(
                    {
                        "status": "executing",
                        "expires_at": datetime.now(UTC) - timedelta(seconds=1),
                    }
                )
                s.commit()
            fresh = _held(entry, "u")  # a new request, not ApprovalBusyError
            assert fresh.id != view.id
            old = get_approval_view("u", view.id)
            assert old.status == "failed" and "unknown" in (old.error or "")

    def test_escalation_gets_the_stricter_window(self):
        with _patch_db(), _danger_tool() as entry:
            _set_mode("u", "agent")
            short = _held(entry, "u")
            longer = _held(entry, "u", url_mode="browser")
            assert longer.expires_at - short.expires_at > timedelta(hours=1)

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
