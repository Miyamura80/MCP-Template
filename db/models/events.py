"""MCP Events (``io.modelcontextprotocol/events``) storage.

``EventLog`` is the one record of every first-party event: polls read it by
cursor (its ``seq``), and webhook fan-out enqueues outbox rows that point at
it. ``EventSubscription`` is a webhook subscription, keyed by the spec's
(principal, url, name, arguments). Deliveries share ``webhook_deliveries``
with the legacy webhook subscriptions, told apart by its ``channel``.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class EventLog(Base):
    __tablename__ = "event_log"

    # The poll cursor. Monotonic per database, opaque to clients.
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, index=True
    )

    __table_args__ = (Index("ix_event_log_user_name_seq", "user_id", "name", "seq"),)


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
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
