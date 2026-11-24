"""Storage for held dangerous calls (``db.models.approvals.PendingAction``).

Status changes go through :func:`transition`, an atomic compare-and-set, so
two requests racing to approve, deny or run the same row can't both win. Used
by ``services/approvals_svc.py``; nothing else should write these rows.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from common import global_config
from db.models.approvals import PendingAction
from models.approvals import ApprovalMode, ApprovalStatus, ApprovalView
from services import InvalidArgument, ServiceEntry, get_registry

# Rows that still decide what a retry of the same call does.
LIVE_STATUSES = ("pending", "approved", "executing", "executed", "denied")


def args_hash(tool: str, args: dict[str, Any]) -> str:
    canonical = json.dumps([tool, args], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def aware(dt: datetime) -> datetime:
    # SQLite drops the zone on DateTime(timezone=True); every value we write is UTC.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def is_expired(action: PendingAction) -> bool:
    return action.status in ("pending", "approved") and aware(
        action.expires_at
    ) <= datetime.now(UTC)


def expiry(mode: ApprovalMode) -> timedelta:
    cfg = global_config.approvals
    seconds = {
        "agent": cfg.agent_expiry_seconds,
        "elicitation": cfg.elicitation_expiry_seconds,
        "browser": cfg.browser_expiry_seconds,
    }[mode]
    return timedelta(seconds=seconds)


def live_action(
    s: Session, user_id: str, tool: str, args_hash: str
) -> PendingAction | None:
    rows = (
        s.query(PendingAction)
        .filter(
            PendingAction.user_id == user_id,
            PendingAction.tool == tool,
            PendingAction.args_hash == args_hash,
            PendingAction.status.in_(LIVE_STATUSES),
        )
        .order_by(PendingAction.created_at.desc())
        .all()
    )
    now = datetime.now(UTC)
    for row in rows:
        if is_expired(row):
            transition(s, row.id, row.status, "expired")
            continue
        # Executed and denied outcomes stay sticky only for the approval's own
        # window; after that the same call is a new request.
        if aware(row.expires_at) > now:
            return row
    return None


def create_action(
    s: Session,
    user_id: str,
    tool: str,
    args: dict[str, Any],
    args_hash: str,
    mode: ApprovalMode,
) -> PendingAction:
    now = datetime.now(UTC)
    action = PendingAction(
        id=f"apr_{secrets.token_urlsafe(16)}",
        user_id=user_id,
        tool=tool,
        args=args,
        args_hash=args_hash,
        mode=mode,
        status="pending",
        created_at=now,
        expires_at=now + expiry(mode),
    )
    s.add(action)
    s.commit()
    return action


def transition(s: Session, approval_id: str, from_: str, to: str) -> bool:
    """Atomic compare-and-set on status. False when another request won."""
    values: dict[str, Any] = {"status": to}
    if to in ("denied", "expired"):
        values["resolved_at"] = datetime.now(UTC)
    count = (
        s.query(PendingAction)
        .filter(PendingAction.id == approval_id, PendingAction.status == from_)
        .update(values, synchronize_session=False)  # ty: ignore[invalid-argument-type]
    )
    s.commit()
    return count == 1


def owned(s: Session, user_id: str, approval_id: str) -> PendingAction | None:
    action = s.get(PendingAction, approval_id)
    if action is None or action.user_id != user_id:
        return None
    if is_expired(action):
        transition(s, approval_id, action.status, "expired")
        return refresh(s, action)
    return action


def refresh(s: Session, action: PendingAction) -> PendingAction:
    s.refresh(action)
    return action


def registry_entry(tool: str) -> ServiceEntry:
    for entry in get_registry():
        if entry.name == tool:
            return entry
    raise InvalidArgument(f"Tool {tool!r} is no longer registered.")


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
