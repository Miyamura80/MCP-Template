"""Approval settings and pending dangerous calls.

``ApprovalSetting`` is a user's floor (set on the dashboard, never by an agent).
``PendingAction`` is one dangerous call held for a decision: the exact
arguments are stored, so whatever runs on approval is what was asked for, and
each row is single use and bound to the user who made the call. See
``services/approvals_svc.py``.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


# A call in one of these states blocks a second row for the same call.
ACTIVE_STATUSES = ("pending", "approved", "executing")
_ACTIVE_SQL = "status IN ('pending', 'approved', 'executing')"


class ApprovalSetting(Base):
    __tablename__ = "approval_settings"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="automatic")
    overrides: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class PendingAction(Base):
    __tablename__ = "pending_actions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tool: Mapped[str] = mapped_column(String(128), nullable=False)
    args: Mapped[dict] = mapped_column(JSON, nullable=False)
    args_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_pending_actions_user_status", "user_id", "status"),
        Index("ix_pending_actions_user_call", "user_id", "tool", "args_hash"),
        # One active row per exact call: two concurrent identical calls must
        # not produce two approvals (and two sends).
        Index(
            "uq_pending_actions_active_call",
            "user_id",
            "tool",
            "args_hash",
            unique=True,
            sqlite_where=text(_ACTIVE_SQL),
            postgresql_where=text(_ACTIVE_SQL),
        ),
    )
