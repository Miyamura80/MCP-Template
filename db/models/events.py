"""MCP Events (``io.modelcontextprotocol/events``) storage.

``EventLog`` is the one record of every first-party event: polls read it by
cursor, and webhook fan-out enqueues outbox rows that point at it. The cursor
is a per-principal counter (``EventCursor.seq``), bumped under that row's lock
by every emit, so one user's events get cursor values in commit order: a
poller can never skip an event that committed late. A global autoincrement
can't promise that, because Postgres hands out sequence values at INSERT. ``EventSubscription`` is a webhook subscription, keyed by the spec's
(principal, url, name, arguments). Deliveries share ``webhook_deliveries``
with the legacy webhook subscriptions, told apart by its ``channel``.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class EventCursor(Base):
    """One row per principal: the last cursor value handed out, and how far
    retention has deleted (a cursor at or below it may have missed events)."""

    __tablename__ = "event_cursors"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pruned_through: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class EventLog(Base):
    __tablename__ = "event_log"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The poll cursor: this principal's counter at emit time.
    user_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, index=True
    )

    __table_args__ = (
        Index("uq_event_log_user_seq", "user_id", "user_seq", unique=True),
        Index("ix_event_log_user_name_seq", "user_id", "name", "user_seq"),
    )


class EventSubscription(Base):
    __tablename__ = "event_subscriptions"

    # Derived from (user_id, url, name, arguments): subscribing again with the
    # same key refreshes this row. Not a capability: knowing it grants nothing.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    arguments: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    # The client-chosen ``whsec_`` secret, Fernet-encrypted at rest.
    secret_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # When the endpoint last passed the challenge (not when it was refreshed).
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (Index("ix_event_subscriptions_user_name", "user_id", "name"),)
