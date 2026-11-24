"""Emit events, read them back by cursor (poll), and age them out.

    emit(user, name, data) ─► event_log row ─┬─► events/poll reads by seq
                                             └─► one webhook_deliveries row
                                                 (channel "mcp_event") per
                                                 matching live subscription

The cursor is the ``event_log.seq`` of the last event a poll consumed, as a
string. ``null`` means "from now". A cursor older than what retention keeps is
answered with ``truncated: true``: the spec's one gap signal.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from common import global_config
from db.engine import use_db_session
from db.models.events import EventLog, EventSubscription
from db.models.webhooks import WebhookDelivery
from services import InvalidArgument
from services.events.catalog import EVENT_TYPES, EventType

MCP_CHANNEL = "mcp_event"
# Rows a poll scans per call; arguments filter after the scan, so this bounds
# the work a poll with a narrow filter can do.
_SCAN_LIMIT = 500


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def event_type(name: str) -> EventType:
    found = EVENT_TYPES.get(name)
    if found is None:
        raise UnknownEventError(name)
    return found


class UnknownEventError(InvalidArgument):
    """No event type by that name (the spec's NotFound, ``kind: "event"``)."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"No event type {name!r}. Call events/list for the types.")


def occurrence(row: EventLog, *, with_cursor: bool) -> dict[str, Any]:
    """The spec's EventOccurrence for one row."""
    body: dict[str, Any] = {
        "eventId": row.event_id,
        "name": row.name,
        "timestamp": _aware(row.created_at).isoformat(),
        "data": row.data,
    }
    if with_cursor:  # push/webhook carry it per event; poll at the top level
        body["cursor"] = str(row.seq)
    return body


def emit(
    user_id: str,
    name: str,
    data: dict[str, Any],
    *,
    session: Session | None = None,
) -> str:
    """Record one event for ``user_id`` and fan it out to webhook subscribers.

    With ``session`` the caller owns the transaction (flush, no commit), so the
    event commits atomically with whatever produced it. Returns the event id.
    """
    if session is None:
        with use_db_session() as own:
            event_id = emit(user_id, name, data, session=own)
            own.commit()
            return event_id

    kind = event_type(name)
    row = EventLog(
        event_id=f"evt_{secrets.token_urlsafe(16)}",
        user_id=user_id,
        name=name,
        data=data,
        created_at=_now(),
    )
    session.add(row)
    session.flush()
    for sub in _live_subscriptions(session, user_id, name):
        if kind.matches(sub.arguments or {}, data):
            enqueue(session, sub.id, row.event_id)
    return row.event_id


def enqueue(session: Session, subscription_id: str, event_id: str) -> None:
    session.add(
        WebhookDelivery(
            id=f"dlv_{secrets.token_urlsafe(16)}",
            event_id=event_id,
            subscription_id=subscription_id,
            channel=MCP_CHANNEL,
            status="pending",
            attempts=0,
            next_attempt_at=_now(),
        )
    )


def _live_subscriptions(
    session: Session, user_id: str, name: str
) -> list[EventSubscription]:
    return (
        session.query(EventSubscription)
        .filter(
            EventSubscription.user_id == user_id,
            EventSubscription.name == name,
            EventSubscription.verified_at.is_not(None),
            EventSubscription.expires_at > _now(),
        )
        .all()
    )


def _parse_cursor(cursor: str | None) -> int | None:
    if cursor is None:
        return None
    try:
        value = int(cursor)
    except ValueError as exc:
        raise InvalidArgument("That cursor did not come from this server.") from exc
    if value < 0:
        raise InvalidArgument("That cursor did not come from this server.")
    return value


def head_cursor(session: Session) -> int:
    """The cursor meaning "now": the newest seq so far (0 when empty)."""
    return int(session.query(func.max(EventLog.seq)).scalar() or 0)


def _truncated(session: Session, after: int) -> bool:
    """Whether retention already dropped events after ``after``."""
    oldest = session.query(func.min(EventLog.seq)).scalar()
    if oldest is None:
        return after < head_cursor(session)
    return after < int(oldest) - 1


def replay_from(
    session: Session, cursor: str | None, max_age_ms: int | None
) -> tuple[int, bool]:
    """Where a poll or subscription starts, and whether that skips events.

    The start is the later of ``cursor`` and now minus ``maxAgeMs`` (spec).
    ``null`` cursor starts from now.
    """
    after = _parse_cursor(cursor)
    if after is None:
        return head_cursor(session), False
    truncated = _truncated(session, after)
    if max_age_ms is not None:
        floor = _now() - timedelta(milliseconds=max_age_ms)
        aged_out = (
            session.query(func.max(EventLog.seq))
            .filter(EventLog.seq > after, EventLog.created_at < floor)
            .scalar()
        )
        if aged_out is not None:
            after, truncated = int(aged_out), True
    return after, truncated


def poll(
    user_id: str,
    name: str,
    arguments: dict[str, Any],
    *,
    cursor: str | None,
    max_age_ms: int | None,
    max_events: int | None,
) -> dict[str, Any]:
    """One ``events/poll``: events after ``cursor`` for this principal."""
    kind = event_type(name)
    arguments = kind.validate_arguments(arguments)
    limit = min(max_events or global_config.events.max_poll_events, 1000)
    with use_db_session() as s:
        after, truncated = replay_from(s, cursor, max_age_ms)
        rows = (
            s.query(EventLog)
            .filter(
                EventLog.user_id == user_id,
                EventLog.name == name,
                EventLog.seq > after,
            )
            .order_by(EventLog.seq)
            .limit(_SCAN_LIMIT)
            .all()
        )
        events: list[dict[str, Any]] = []
        position = after
        for row in rows:
            if len(events) == limit:
                break
            position = row.seq
            if kind.matches(arguments, row.data):
                events.append(occurrence(row, with_cursor=False))
        # More when we stopped before the scanned rows ran out, or the scan
        # itself was cut at its limit.
        has_more = bool(rows) and (position != rows[-1].seq or len(rows) == _SCAN_LIMIT)
        if not rows:
            # Nothing of ours: move to the global head so a later poll never
            # rescans other tenants' rows.
            position = max(position, head_cursor(s))
    return {
        "resultType": "complete",
        "events": events,
        "cursor": str(position),
        "truncated": truncated,
        "hasMore": has_more,
        "nextPollMs": 0 if has_more else global_config.events.poll_interval_ms,
    }


def prune(older_than: timedelta | None = None) -> int:
    """Drop events past retention, and their now-pointless outbox rows."""
    cutoff = _now() - (
        older_than or timedelta(days=global_config.events.retention_days)
    )
    with use_db_session() as s:
        stale = s.query(EventLog.event_id).filter(EventLog.created_at < cutoff)
        s.query(WebhookDelivery).filter(
            WebhookDelivery.channel == MCP_CHANNEL,
            WebhookDelivery.event_id.in_(stale.scalar_subquery()),
        ).delete(synchronize_session=False)
        removed = (
            s.query(EventLog)
            .filter(EventLog.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        s.query(EventSubscription).filter(
            EventSubscription.expires_at < _now() - timedelta(days=1)
        ).delete(synchronize_session=False)
        s.commit()
    return int(removed)


def purge_user(user_id: str, name_prefix: str) -> int:
    """Delete a user's events under a name prefix (e.g. on Gmail disconnect:
    those payloads bank message content)."""
    with use_db_session() as s:
        mine = s.query(EventLog.event_id).filter(
            EventLog.user_id == user_id, EventLog.name.startswith(name_prefix)
        )
        s.query(WebhookDelivery).filter(
            WebhookDelivery.channel == MCP_CHANNEL,
            WebhookDelivery.event_id.in_(mine.scalar_subquery()),
        ).delete(synchronize_session=False)
        removed = (
            s.query(EventLog)
            .filter(EventLog.user_id == user_id, EventLog.name.startswith(name_prefix))
            .delete(synchronize_session=False)
        )
        s.commit()
    return int(removed)
