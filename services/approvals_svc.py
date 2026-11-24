"""Human-in-the-loop approval for ``risk="dangerous"`` tools.

    tools/call(gmail_send) ──► check_gate ── mode = stricter(dashboard, ?approval=)
                                  │
       automatic ─────────────────┤ Proceed: run now
       agent / elicitation /      │ hold: PendingAction(pending) + ApprovalRequiredError
       browser                    │
                                  ▼
       confirm_action (agent) ─┐
       host prompt (elicit) ───┼─► pending -> approved      (nothing runs here)
       dashboard (browser) ────┘
                                  │
       retry of the same call ────┴─► check_gate: RunHeld -> run via the
                                      transport (metered), record the outcome
       later retries ──────────────► Replay the stored result

There is one way an approved call runs: the caller retries it, and it goes
through the normal transport path with its metering and error handling. The
arguments that run are the stored ones (same tool, same arguments, same user),
so nothing can be swapped between the request and the approval. Every status
change is a compare-and-set, and one active row per exact call is enforced by
a unique index.

Retrying a held call never asks twice: a pending one raises the same approval,
an approved one runs, an executed one replays its result, a denied one stays
denied. If the mode got stricter since the row was made (the dashboard was
tightened, or this connection asks ``?approval=browser``), the row moves to the
stricter mode and any looser approval is dropped.

``user_id=None`` means a local, unauthenticated transport (the CLI, stdio MCP):
the operator at their own machine, never gated, like it is never metered.
Authenticated transports always pass the user.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from db.engine import use_db_session
from db.models.approvals import ApprovalSetting, PendingAction
from models.approvals import (
    ApprovalIdInput,
    ApprovalMode,
    ApprovalSettings,
    ApprovalView,
    stricter,
)
from services import InvalidArgument, ServiceEntry, dangerous_tool_names, service
from services.approvals_errors import (
    ApprovalBusyError,
    ApprovalDeniedError,
    required,
)
from services.approvals_store import (
    args_hash,
    create_action,
    escalate,
    find_call,
    owned,
    record,
    shown_args,
    to_view,
    transition,
)
from services.events import emit
from services.events.catalog import APPROVAL_RESOLVED

# Helper tools for the flow. Hidden alongside dangerous tools when a connection
# sets ``dangerous_tools=false``, since they only exist to run those.
APPROVAL_TOOLS = frozenset({"confirm_action", "get_approval"})


# ---------------------------------------------------------------------------
# Settings (dashboard only; agents never write these)
# ---------------------------------------------------------------------------


def _load_settings(s: Session, user_id: str) -> ApprovalSettings:
    row = s.get(ApprovalSetting, user_id)
    if row is None:
        return ApprovalSettings()
    return ApprovalSettings.model_validate(
        {"mode": row.mode, "overrides": row.overrides or {}}
    )


def get_settings(user_id: str) -> ApprovalSettings:
    with use_db_session() as s:
        return _load_settings(s, user_id)


def save_settings(user_id: str, settings: ApprovalSettings) -> ApprovalSettings:
    unknown = set(settings.overrides) - dangerous_tool_names()
    if unknown:
        raise InvalidArgument(
            f"Overrides apply to dangerous tools only; not dangerous: "
            f"{', '.join(sorted(unknown))}."
        )
    with use_db_session() as s:
        row = s.get(ApprovalSetting, user_id)
        if row is None:
            row = ApprovalSetting(user_id=user_id)
            s.add(row)
        row.mode = settings.mode
        row.overrides = dict(settings.overrides)
        s.commit()
    return settings


def _floor(s: Session, user_id: str, tool: str) -> ApprovalMode:
    settings = _load_settings(s, user_id)
    return settings.overrides.get(tool, settings.mode)


def effective_mode(
    user_id: str, tool: str, url_mode: ApprovalMode | None
) -> ApprovalMode:
    """The dashboard floor for ``tool``, tightened (never loosened) by the URL."""
    with use_db_session() as s:
        return stricter(_floor(s, user_id, tool), url_mode)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Proceed:
    """Run the call; nothing to record."""


@dataclass(frozen=True)
class RunHeld:
    """Run the approved held call, then record its outcome on ``action_id``."""

    action_id: str


@dataclass(frozen=True)
class Replay:
    """Return this stored output instead of running again."""

    output: dict[str, Any]


GateDecision = Proceed | RunHeld | Replay
PROCEED = Proceed()


def check_gate(
    entry: ServiceEntry,
    input_obj: BaseModel,
    *,
    user_id: str | None,
    url_mode: ApprovalMode | None,
    can_confirm_inline: bool,
) -> GateDecision:
    """Decide what a call does now. Raises an ``ApprovalGateError`` when held.

    ``can_confirm_inline``: the transport can put a confirmation in front of
    the human right now (an MCP host that can elicit). When it can't, an
    elicitation-mode call escalates to browser, never to a looser mode.
    """
    if entry.risk != "dangerous" or user_id is None:
        return PROCEED
    if not user_id:
        raise InvalidArgument("A dangerous call needs an authenticated user.")

    args = input_obj.model_dump(mode="json")
    call_hash = args_hash(entry.name, args)
    with use_db_session() as s:
        mode = stricter(_floor(s, user_id, entry.name), url_mode)
        if mode == "elicitation" and not can_confirm_inline:
            mode = "browser"
        row = find_call(s, user_id, entry.name, call_hash)
        if row is None:
            if mode == "automatic":
                return PROCEED
            row = create_action(s, user_id, entry.name, args, call_hash, mode)
        return _resume(s, row, mode)


def _resume(s: Session, row: PendingAction, mode: ApprovalMode) -> GateDecision:
    """What this call does given the row already held for it."""
    if row.status in ("pending", "approved"):
        current: ApprovalMode = row.mode  # ty: ignore[invalid-assignment]
        if stricter(current, mode) != current:
            escalate(s, row, mode)  # loses to a concurrent change: re-read either way
            s.refresh(row)
        elif row.status == "approved":
            return RunHeld(row.id)
        raise required(to_view(row), row.args_hash, row.user_id)
    if row.status == "executed" and row.result is not None:
        return Replay(row.result)
    if row.status == "denied":
        raise ApprovalDeniedError(
            f"The user denied this {row.tool} call (approval {row.id}). "
            "Do not retry it; ask the user what they want instead."
        )
    raise ApprovalBusyError(
        f"This {row.tool} call is already running (approval {row.id}). "
        f"Check it with get_approval(approval_id='{row.id}')."
    )


@contextmanager
def running(decision: GateDecision) -> Iterator[Callable[[BaseModel], None]]:
    """Claim an approved call for the duration of its run and record how it ended.

    Yields ``done(result)``; call it with the tool's output. Anything else
    (an exception, a cancelled request) records the call as failed, so a row
    is never left ``executing``. A no-op for anything but ``RunHeld``.
    """
    if not isinstance(decision, RunHeld):
        yield lambda _result: None
        return
    approval_id = decision.action_id
    with use_db_session() as s:
        if not transition(s, approval_id, "approved", "executing"):
            raise ApprovalBusyError(
                f"Approval {approval_id} is no longer runnable (it is running, "
                "expired or was denied). Check it with get_approval."
            )
    finished = False

    def done(result: BaseModel) -> None:
        nonlocal finished
        with use_db_session() as s:
            record(s, approval_id, result=result.model_dump(mode="json"), error=None)
        finished = True

    try:
        yield done
    finally:
        if not finished:
            with use_db_session() as s:
                record(s, approval_id, result=None, error="The call failed.")


# ---------------------------------------------------------------------------
# Decisions (none of these run anything; the retried call does)
# ---------------------------------------------------------------------------


def _announce(s: Session, action: PendingAction, status: str) -> None:
    """Tell subscribers (MCP Events) that a held call was decided, so a
    background agent can retry it without polling ``get_approval``."""
    emit(
        action.user_id,
        APPROVAL_RESOLVED,
        {"approvalId": action.id, "tool": action.tool, "status": status},
        session=s,
    )
    s.commit()


def _approve(s: Session, user_id: str, approval_id: str) -> ApprovalView:
    action = owned(s, user_id, approval_id)
    if action is None:
        raise InvalidArgument(f"No approval {approval_id!r} for this user.")
    if transition(s, approval_id, "pending", "approved"):
        _announce(s, action, "approved")
    s.refresh(action)
    return to_view(action)


def approve_as_human(user_id: str, approval_id: str) -> ApprovalView:
    """The dashboard: a signed-in human may approve a call in any mode."""
    with use_db_session() as s:
        return _approve(s, user_id, approval_id)


def approve_elicitation(
    user_id: str, approval_id: str, tool: str, call_hash: str
) -> bool:
    """The host relayed a human "yes" for exactly this call."""
    with use_db_session() as s:
        action = owned(s, user_id, approval_id)
        if (
            action is None
            or action.mode != "elicitation"
            or action.tool != tool
            or action.args_hash != call_hash
        ):
            return False
        if not transition(s, approval_id, "pending", "approved"):
            return False
        _announce(s, action, "approved")
        return True


def deny(user_id: str, approval_id: str, *, tool: str | None = None) -> ApprovalView:
    with use_db_session() as s:
        action = owned(s, user_id, approval_id)
        if action is None or (tool is not None and action.tool != tool):
            raise InvalidArgument(f"No approval {approval_id!r} for this user.")
        if transition(s, approval_id, "pending", "denied") or transition(
            s, approval_id, "approved", "denied"
        ):
            _announce(s, action, "denied")
        s.refresh(action)
        return to_view(action)


def get_approval_view(user_id: str, approval_id: str) -> ApprovalView:
    with use_db_session() as s:
        action = owned(s, user_id, approval_id)
        if action is None:
            raise InvalidArgument(f"No approval {approval_id!r} for this user.")
        return to_view(action)


def get_review(user_id: str, approval_id: str) -> tuple[ApprovalView, dict[str, Any]]:
    """One read for a human reviewing a call: its state and exact arguments."""
    with use_db_session() as s:
        action = owned(s, user_id, approval_id)
        if action is None:
            raise InvalidArgument(f"No approval {approval_id!r} for this user.")
        return to_view(action), shown_args(action)


def list_approvals(user_id: str, limit: int = 50) -> list[ApprovalView]:
    with use_db_session() as s:
        rows = (
            s.query(PendingAction)
            .filter(PendingAction.user_id == user_id)
            .order_by(PendingAction.created_at.desc())
            .limit(limit)
            .all()
        )
        return [to_view(r) for r in rows]


# ---------------------------------------------------------------------------
# Agent-facing tools
# ---------------------------------------------------------------------------


@service(
    name="confirm_action",
    description=(
        "Approve a dangerous call that is waiting for your confirmation (agent "
        "approval mode), then retry that exact call to run it. Only confirm when "
        "the held arguments match what the user asked for. Calls held for the "
        "user's own approval cannot be confirmed here."
    ),
    input_model=ApprovalIdInput,
    output_model=ApprovalView,
    mutating=True,
)
def confirm_action(input: ApprovalIdInput) -> ApprovalView:
    with use_db_session() as s:
        action = owned(s, input.user_id, input.approval_id)
        if action is None:
            raise InvalidArgument(f"No approval {input.approval_id!r} for this user.")
        # The row's mode, and the user's current floor, must both still allow
        # an agent to approve. A tightened dashboard revokes agent approval.
        floor = _floor(s, input.user_id, action.tool)
        if action.mode != "agent" or stricter(floor, "agent") != "agent":
            raise InvalidArgument(
                f"Approval {input.approval_id} needs the user, not the agent. "
                "Give them its link and check back with get_approval."
            )
        return _approve(s, input.user_id, input.approval_id)


@service(
    name="get_approval",
    description=(
        "Check a held dangerous call: still pending, approved (retry the call to "
        "run it), executed (with its result), denied, or expired."
    ),
    input_model=ApprovalIdInput,
    output_model=ApprovalView,
)
def get_approval(input: ApprovalIdInput) -> ApprovalView:
    return get_approval_view(input.user_id, input.approval_id)
