"""Human-in-the-loop approval for ``risk="dangerous"`` tools.

    tools/call(gmail_send) ──► check_gate ──► mode = stricter(dashboard, ?approval=)
                                  │
         automatic ───────────────┤ run now
         agent ───────────────────┤ PendingAction ─► agent calls confirm_action(id)
         elicitation ─────────────┤ PendingAction ─► host asks the human (MCP only;
                                  │                  no inline channel -> browser)
         browser ─────────────────┘ PendingAction ─► human approves on a dashboard
                                                     page; the server runs it

Every non-automatic call stores the exact arguments, so what runs on approval
is what was asked for. Rows are single use (status transitions are atomic
compare-and-set updates) and bound to the user who made the call.

Retrying the same call (same tool, same arguments) while its approval is live
never creates a second one: a pending call raises the same approval again, an
executed one replays its stored result, a denied one stays denied. That makes a
plain retry the natural way to pick up a browser approval, and it stops a
retry loop from sending the same email twice.

The transports own the "how": the MCP layer turns an elicitation-mode approval
into a host prompt and a browser-mode one into a URL elicitation; REST answers
``202 Accepted``. The CLI is the operator at their own terminal and is never
gated, the same way it is never metered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from loguru import logger as log
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db.engine import use_db_session
from db.models.approvals import ApprovalSetting, PendingAction
from models.approvals import (
    ApprovalIdInput,
    ApprovalMode,
    ApprovalSettings,
    ApprovalStatus,
    ApprovalView,
    stricter,
)
from services import (
    ConnectRequiredError,
    InvalidArgument,
    ModelFacingError,
    ServiceEntry,
    get_registry,
    service,
)
from services.approvals_store import (
    args_hash,
    create_action,
    live_action,
    owned,
    refresh,
    registry_entry,
    to_view,
    transition,
)

# Helper tools for the flow. Hidden alongside dangerous tools when a connection
# sets ``dangerous_tools=false``, since they only exist to run those.
APPROVAL_TOOLS = frozenset({"confirm_action", "get_approval"})


# ---------------------------------------------------------------------------
# Errors the transports translate
# ---------------------------------------------------------------------------


class ApprovalGateError(ModelFacingError):
    """The gate stopped a call before it ran (held, denied, or already running).

    Nothing happened yet, so transports treat it like a rejected argument: the
    REST idempotency claim is released and the call may be retried.
    """


class ApprovalBusyError(ApprovalGateError):
    """The same approved call is running right now."""


class ApprovalRequiredError(ApprovalGateError):
    """A dangerous call is held for approval. The message tells the agent what to do."""

    def __init__(self, view: ApprovalView) -> None:
        self.view = view
        # Not super(): the browser subclass also inherits ConnectRequiredError,
        # whose __init__ takes different arguments.
        ModelFacingError.__init__(self, _instructions(view))


class BrowserApprovalRequiredError(ApprovalRequiredError, ConnectRequiredError):
    """Browser-mode approval: also a connect-style flow, so MCP hosts that
    support URL elicitation open the approval page natively."""

    def __init__(self, view: ApprovalView, user_id: str) -> None:
        self.view = view
        ConnectRequiredError.__init__(
            self,
            user_id,
            _instructions(view),
            elicitation_message=(
                f"Approve {view.tool} before it runs. Open the page to approve or deny."
            ),
        )

    def build_auth_url(self) -> str | None:
        return self.view.approve_url


class ApprovalDeniedError(ApprovalGateError):
    """A human denied this exact call. Retrying it will not help."""


def _instructions(view: ApprovalView) -> str:
    expires = view.expires_at.isoformat(timespec="seconds")
    if view.mode == "agent":
        return (
            f"{view.tool} needs confirmation before it runs. Re-read the arguments "
            f"you sent; if they are what the user wants, call "
            f"confirm_action(approval_id='{view.id}'). Expires {expires}."
        )
    if view.mode == "elicitation":
        return (
            f"{view.tool} is waiting for the user to confirm in this app "
            f"(approval {view.id}, expires {expires})."
        )
    return (
        f"{view.tool} is waiting for the user to approve it at {view.approve_url} "
        f"(approval {view.id}, expires {expires}). Give the user that link. "
        f"Check the outcome with get_approval(approval_id='{view.id}'), or retry "
        "this same call after they approve to get its result."
    )


# ---------------------------------------------------------------------------
# Settings (dashboard only; agents never write these)
# ---------------------------------------------------------------------------


def _dangerous_tools() -> set[str]:
    return {e.name for e in get_registry() if e.risk == "dangerous"}


def get_settings(user_id: str) -> ApprovalSettings:
    with use_db_session() as s:
        row = s.get(ApprovalSetting, user_id)
        if row is None:
            return ApprovalSettings()
        return ApprovalSettings.model_validate(
            {"mode": row.mode, "overrides": row.overrides or {}}
        )


def save_settings(user_id: str, settings: ApprovalSettings) -> ApprovalSettings:
    unknown = set(settings.overrides) - _dangerous_tools()
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


def effective_mode(
    user_id: str, tool: str, url_mode: ApprovalMode | None
) -> ApprovalMode:
    """The dashboard floor for ``tool``, tightened (never loosened) by the URL."""
    settings = get_settings(user_id)
    floor = settings.overrides.get(tool, settings.mode)
    return stricter(floor, url_mode)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateDecision:
    """``proceed``: run the tool. ``action_id`` set: record the outcome on it.
    ``replay`` set: return this stored output instead of running."""

    action_id: str | None = None
    replay: dict[str, Any] | None = None


PROCEED = GateDecision()


def check_gate(
    entry: ServiceEntry,
    input_obj: BaseModel,
    *,
    user_id: str | None,
    url_mode: ApprovalMode | None,
    can_confirm_inline: bool,
) -> GateDecision:
    """Decide whether a call runs now. Raises ``ApprovalRequiredError`` or
    ``ApprovalDeniedError`` when it doesn't.

    ``can_confirm_inline`` is whether the transport can ask the human right
    now (an MCP host that supports elicitation). When it can't, an
    elicitation-mode call escalates to browser, never to a looser mode.
    """
    if entry.risk != "dangerous" or not user_id:
        return PROCEED

    args = input_obj.model_dump(mode="json")
    call_hash = args_hash(entry.name, args)
    with use_db_session() as s:
        existing = live_action(s, user_id, entry.name, call_hash)
        if existing is not None:
            decision = _resume(s, existing)
            if decision is not None:
                return decision

        mode = effective_mode(user_id, entry.name, url_mode)
        if mode == "automatic":
            return PROCEED
        if mode == "elicitation" and not can_confirm_inline:
            mode = "browser"
        action = create_action(s, user_id, entry.name, args, call_hash, mode)
        view = to_view(action)
    raise _required(view, user_id)


def _resume(s: Session, action: PendingAction) -> GateDecision | None:
    """What a retry of an already-gated call does. ``None``: gate it afresh."""
    status = action.status
    if status == "approved" and transition(s, action.id, "approved", "executing"):
        return GateDecision(action_id=action.id)
    if status == "executed":
        return GateDecision(replay=action.result or {})
    if status == "denied":
        raise ApprovalDeniedError(
            f"The user denied this {action.tool} call (approval {action.id}). "
            "Do not retry it; ask the user what they want instead."
        )
    if status in ("pending", "approved"):
        raise _required(to_view(action), action.user_id)
    if status == "executing":
        raise ApprovalBusyError(
            f"This {action.tool} call is already running (approval {action.id}). "
            f"Check it with get_approval(approval_id='{action.id}')."
        )
    return None  # failed: a fresh attempt may be approved again


def record_outcome(
    action_id: str, *, result: BaseModel | None = None, error: str | None = None
) -> None:
    """Store how an approved call ended, so retries and ``get_approval`` see it."""
    with use_db_session() as s:
        action = s.get(PendingAction, action_id)
        if action is None:
            return
        action.status = "failed" if error is not None else "executed"
        action.result = result.model_dump(mode="json") if result is not None else None
        action.error = error
        action.resolved_at = datetime.now(UTC)
        s.commit()


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def approve_elicitation(user_id: str, approval_id: str, tool: str) -> bool:
    """The host relayed a human "yes". The retried call then runs it."""
    with use_db_session() as s:
        action = owned(s, user_id, approval_id)
        if action is None or action.mode != "elicitation" or action.tool != tool:
            return False
        return transition(s, approval_id, "pending", "approved")


def approve_and_execute(
    user_id: str, approval_id: str, *, modes: frozenset[str]
) -> ApprovalView:
    """Run a pending call now. ``modes`` limits who may do this: an agent may
    only confirm agent-mode calls; a human on the dashboard may approve any."""
    with use_db_session() as s:
        action = owned(s, user_id, approval_id)
        if action is None:
            raise InvalidArgument(f"No approval {approval_id!r} for this user.")
        if action.mode not in modes:
            raise InvalidArgument(
                f"Approval {approval_id} is in {action.mode} mode, so only the user "
                "can approve it."
            )
        if not transition(s, approval_id, "pending", "executing"):
            return to_view(refresh(s, action))
        tool = action.tool
        entry = registry_entry(tool)
        input_model: type[BaseModel] = entry.input_model  # ty: ignore[invalid-assignment]
        args = dict(action.args)
        if "user_id" in input_model.model_fields:
            args["user_id"] = action.user_id  # never trust a stored user_id
        input_obj = input_model.model_validate(args)
    try:
        record_outcome(approval_id, result=entry.func(input_obj))
    except ModelFacingError as exc:
        record_outcome(approval_id, error=str(exc))
    except Exception:  # noqa: BLE001
        # Approval boundary: the stored call is arbitrary service code. Record
        # the failure on the row (agents poll it) instead of crashing the
        # confirm call or the dashboard request; the traceback goes to logs.
        log.exception("approved {} call {} failed", tool, approval_id)
        record_outcome(approval_id, error=f"{tool} failed.")
    return get_approval_view(user_id, approval_id)


def deny(user_id: str, approval_id: str) -> ApprovalView:
    with use_db_session() as s:
        action = owned(s, user_id, approval_id)
        if action is None:
            raise InvalidArgument(f"No approval {approval_id!r} for this user.")
        for current in ("pending", "approved"):
            if transition(s, approval_id, current, "denied"):
                break
        return to_view(refresh(s, action))


def get_approval_view(user_id: str, approval_id: str) -> ApprovalView:
    with use_db_session() as s:
        action = owned(s, user_id, approval_id)
        if action is None:
            raise InvalidArgument(f"No approval {approval_id!r} for this user.")
        return to_view(action)


def get_action_args(user_id: str, approval_id: str) -> dict[str, Any] | None:
    """The stored arguments, for the dashboard's review page."""
    with use_db_session() as s:
        action = owned(s, user_id, approval_id)
        return dict(action.args) if action is not None else None


def list_approvals(
    user_id: str, status: ApprovalStatus | None = None, limit: int = 50
) -> list[ApprovalView]:
    with use_db_session() as s:
        rows = (
            s.query(PendingAction)
            .filter(PendingAction.user_id == user_id)
            .order_by(PendingAction.created_at.desc())
            .limit(limit)
            .all()
        )
        views = [to_view(r) for r in rows]
    return [v for v in views if status is None or v.status == status]


# ---------------------------------------------------------------------------
# Agent-facing tools
# ---------------------------------------------------------------------------


@service(
    name="confirm_action",
    description=(
        "Run a dangerous call that is waiting for your confirmation (agent "
        "approval mode). Only confirm when the held arguments match what the "
        "user asked for. Calls held for the user's own approval cannot be "
        "confirmed here."
    ),
    input_model=ApprovalIdInput,
    output_model=ApprovalView,
    mutating=True,
)
def confirm_action(input: ApprovalIdInput) -> ApprovalView:
    return approve_and_execute(
        input.user_id, input.approval_id, modes=frozenset({"agent"})
    )


@service(
    name="get_approval",
    description=(
        "Check a held dangerous call: whether it is still pending, was approved "
        "and ran (with its result), was denied, or expired."
    ),
    input_model=ApprovalIdInput,
    output_model=ApprovalView,
)
def get_approval(input: ApprovalIdInput) -> ApprovalView:
    return get_approval_view(input.user_id, input.approval_id)


def _required(view: ApprovalView, user_id: str) -> ApprovalRequiredError:
    if view.mode == "browser":
        return BrowserApprovalRequiredError(view, user_id)
    return ApprovalRequiredError(view)
