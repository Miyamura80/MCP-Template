"""Storage for held dangerous calls (``db.models.approvals.PendingAction``).

Every status change is a compare-and-set (:func:`transition`), and changes out
of ``pending``/``approved`` also require the row to be unexpired, so two
requests racing on one row can't both win and a row can't be approved in the
instant it expires. A partial unique index allows one active row per
(user, tool, arguments); :func:`create_action` returns the winner when a
concurrent identical call got there first.

Expiry is derived on read (:func:`to_view`) and only persisted by
:func:`sweep_stale`, which runs before a new row is created.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from common import global_config
from db.models.approvals import ACTIVE_STATUSES, PendingAction
from models.approvals import ApprovalMode, ApprovalStatus, ApprovalView

_RUN_WINDOW = timedelta(minutes=10)
# Outcomes a retry of the same call still sees, within the approval's window.
STICKY_STATUSES = ("executed", "denied")
_UNKNOWN_OUTCOME = (
    "The server stopped while running this call, so whether it took effect is "
    "unknown. Check before trying again."
)


def args_hash(tool: str, args: dict[str, Any]) -> str:
    canonical = json.dumps([tool, args], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def aware(dt: datetime) -> datetime:
    # SQLite drops the zone on DateTime(timezone=True); every value we write is UTC.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def is_expired(action: PendingAction) -> bool:
    return (
        action.status in ("pending", "approved") and aware(action.expires_at) <= _now()
    )


def expiry(mode: ApprovalMode) -> timedelta:
    cfg = global_config.approvals
    seconds = {
        "agent": cfg.agent_expiry_seconds,
        "elicitation": cfg.elicitation_expiry_seconds,
        "browser": cfg.browser_expiry_seconds,
    }[mode]
    return timedelta(seconds=seconds)


def find_call(
    s: Session, user_id: str, tool: str, call_hash: str
) -> PendingAction | None:
    """The row that decides what this exact call does now, if any."""
    row = (
        s.query(PendingAction)
        .filter(
            PendingAction.user_id == user_id,
            PendingAction.tool == tool,
            PendingAction.args_hash == call_hash,
            PendingAction.status.in_(ACTIVE_STATUSES + STICKY_STATUSES),
        )
        .order_by(PendingAction.created_at.desc())
        .first()
    )
    if row is None:
        return None
    if aware(row.expires_at) > _now():
        return row
    if row.status == "executing":
        # Still running after its run window: the process died mid-call.
        # Record "outcome unknown" (the human sees it in the dashboard list)
        # and treat the retry as a new request.
        sweep_stale(s, user_id, tool, call_hash)
    return None  # expired request, or an outcome past its window


def sweep_stale(s: Session, user_id: str, tool: str, call_hash: str) -> None:
    """Persist expiry for this call's rows, freeing the active slot."""
    same_call = (
        PendingAction.user_id == user_id,
        PendingAction.tool == tool,
        PendingAction.args_hash == call_hash,
        PendingAction.expires_at <= _now(),
    )
    s.query(PendingAction).filter(
        *same_call, PendingAction.status.in_(("pending", "approved"))
    ).update(
        {"status": "expired", "resolved_at": _now()},
        synchronize_session=False,
    )
    # A row still "executing" after its window means the process died mid-call.
    s.query(PendingAction).filter(
        *same_call, PendingAction.status == "executing"
    ).update(
        {"status": "failed", "error": _UNKNOWN_OUTCOME, "resolved_at": _now()},
        synchronize_session=False,
    )
    s.commit()


def create_action(
    s: Session,
    user_id: str,
    tool: str,
    args: dict[str, Any],
    call_hash: str,
    mode: ApprovalMode,
) -> PendingAction:
    """Insert a pending row, or return the one a concurrent identical call made."""
    sweep_stale(s, user_id, tool, call_hash)
    now = _now()
    action = PendingAction(
        id=f"apr_{secrets.token_urlsafe(16)}",
        user_id=user_id,
        tool=tool,
        args=args,
        args_hash=call_hash,
        mode=mode,
        status="pending",
        created_at=now,
        expires_at=now + expiry(mode),
    )
    s.add(action)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        winner = find_call(s, user_id, tool, call_hash)
        if winner is None:  # pragma: no cover - the unique index guarantees one
            raise
        return winner
    return action


def transition(s: Session, approval_id: str, from_: str, to: str) -> bool:
    """Compare-and-set on status. False when another request won, or when
    leaving ``pending``/``approved`` from a row that has expired."""
    values: dict[str, Any] = {"status": to}
    if to in ("denied", "executed", "failed"):
        values["resolved_at"] = _now()
    if to == "executing":
        # A run gets its own window, so a long-running call near its approval
        # deadline isn't mistaken for a crashed one by a concurrent retry.
        values["expires_at"] = _now() + _RUN_WINDOW
    query = s.query(PendingAction).filter(
        PendingAction.id == approval_id, PendingAction.status == from_
    )
    if from_ in ("pending", "approved"):
        query = query.filter(PendingAction.expires_at > _now())
    count = query.update(values, synchronize_session=False)  # ty: ignore[invalid-argument-type]
    s.commit()
    return count == 1


def record(
    s: Session, approval_id: str, *, result: dict | None, error: str | None
) -> None:
    """``executing`` -> ``executed`` or ``failed``. A no-op on any other status."""
    s.query(PendingAction).filter(
        PendingAction.id == approval_id, PendingAction.status == "executing"
    ).update(
        {
            "status": "failed" if error is not None else "executed",
            "result": result,
            "error": error,
            "resolved_at": _now(),
        },
        synchronize_session=False,
    )
    s.commit()


def escalate(s: Session, action: PendingAction, mode: ApprovalMode) -> bool:
    """Move an undecided row to a stricter mode. An agent's approval made under
    the looser mode no longer counts, so the row goes back to ``pending``."""
    count = (
        s.query(PendingAction)
        .filter(
            PendingAction.id == action.id,
            PendingAction.mode == action.mode,
            or_(PendingAction.status == "pending", PendingAction.status == "approved"),
        )
        .update(
            # A fresh window for the stricter mode (a browser link lasts 24 h).
            {"mode": mode, "status": "pending", "expires_at": _now() + expiry(mode)},
            synchronize_session=False,
        )
    )
    s.commit()
    return count == 1


def owned(s: Session, user_id: str, approval_id: str) -> PendingAction | None:
    action = s.get(PendingAction, approval_id)
    if action is None or action.user_id != user_id:
        return None
    return action


def _public_base() -> str:
    configured = global_config.MCP_PUBLIC_URL
    if configured:
        parts = urlsplit(configured)
        return f"{parts.scheme}://{parts.netloc}"
    return f"http://localhost:{global_config.server.port}"


def approve_url(approval_id: str) -> str:
    return global_config.approvals.approve_url_template.format(
        base=_public_base(), id=approval_id
    )


def to_view(action: PendingAction) -> ApprovalView:
    status: ApprovalStatus = "expired" if is_expired(action) else action.status  # ty: ignore[invalid-assignment]
    mode: ApprovalMode = action.mode  # ty: ignore[invalid-assignment]
    return ApprovalView(
        id=action.id,
        tool=action.tool,
        mode=mode,
        status=status,
        created_at=aware(action.created_at),
        expires_at=aware(action.expires_at),
        approve_url=approve_url(action.id) if mode != "agent" else None,
        result=action.result,
        error=action.error,
    )


def shown_args(action: PendingAction) -> dict[str, Any]:
    """The stored arguments as a human reviews them (no injected user id)."""
    return {k: v for k, v in (action.args or {}).items() if k != "user_id"}
