"""Emit events, read them back by cursor (poll), and age them out.

    emit(user, name, data) ─► lock event_cursors[user], seq += 1
                              ─► event_log row (user_seq = seq) ─┬─► events/poll
                              └─► one webhook_deliveries row per  │   reads
                                  matching live subscription      │   user_seq > cursor

The cursor is the principal's own counter, as a string. Because every emit for
a user bumps it under that row's lock, a user's events commit in cursor order,
so "everything after cursor N" is never missing a row that commits later.
``null`` means "from now". ``prune`` records how far it deleted
(``pruned_through``); a cursor below that may have missed events and gets
``truncated: true``, the spec's one gap signal.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from common import global_config
from db.engine import use_db_session
from db.models.events import EventCursor, EventLog, EventSubscription
from db.models.webhooks import WebhookDelivery
from services import InvalidArgument
from services.events.catalog import EVENT_TYPES, EventType

MCP_CHANNEL = "mcp_event"
# Rows a poll scans per call; arguments filter after the scan, so this bounds
# the work a poll with a narrow filter can do.
_SCAN_LIMIT = 500


def now() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime) -> datetime:
    # SQLite drops the zone on DateTime(timezone=True); every value we write is UTC.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class UnknownEventError(InvalidArgument):
    """No event type by that name (the spec's NotFound, ``kind: "event"``)."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"No event type {name!r}. Call events/list for the types.")


def event_type(name: str) -> EventType:
    found = EVENT_TYPES.get(name)
    if found is None:
        raise UnknownEventError(name)
    return found


def occurrence(row: EventLog) -> dict[str, Any]:
    """The spec's EventOccurrence for one row (push/webhook add ``cursor``)."""
    return {
        "eventId": row.event_id,
        "name": row.name,
        "timestamp": aware(row.created_at).isoformat(),
        "data": row.data,
    }


def lock_cursor(s: Session, user_id: str) -> EventCursor:
    """This principal's cursor row, locked until the transaction ends.

    Serializes the user's emits (and subscribes, which must see a consistent
    head) without touching anyone else's. The first-insert race relies on READ
    COMMITTED (the default): the re-query sees the winner's committed row.
    Under REPEATABLE READ or SERIALIZABLE it would need a retry.
    """
    query = s.query(EventCursor).filter_by(user_id=user_id).with_for_update()
    row = query.one_or_none()
    if row is not None:
        return row
    try:
        with s.begin_nested():  # a concurrent first emit may insert it first
            s.add(EventCursor(user_id=user_id, seq=0, pruned_through=0))
    except IntegrityError:
        pass
    return query.one()


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
    cursor = lock_cursor(session, user_id)
    cursor.seq += 1
    row = EventLog(
        event_id=f"evt_{secrets.token_urlsafe(16)}",
        user_seq=cursor.seq,
        user_id=user_id,
        name=name,
        data=data,
        created_at=now(),
    )
    session.add(row)
    for sub in live_subscriptions(session, user_id, name):
        if kind.matches(sub.arguments or {}, data):
            enqueue(session, sub.id, row.event_id)
    session.flush()
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
            next_attempt_at=now(),
        )
    )


def live_subscriptions(
    session: Session, user_id: str, name: str
) -> list[EventSubscription]:
    return (
        session.query(EventSubscription)
        .filter(
            EventSubscription.user_id == user_id,
            EventSubscription.name == name,
            EventSubscription.expires_at > now(),
        )
        .all()
    )


def start_after(
    s: Session,
    cursor_row: EventCursor | None,
    user_id: str,
    name: str,
    cursor: str | None,
    max_age_ms: int | None,
) -> tuple[int, bool]:
    """Where a poll or subscription starts, and whether that skips events.

    The start is the later of ``cursor`` and now minus ``maxAgeMs`` (spec);
    ``null`` starts from now. A cursor this server never issued is refused.
    """
    head = cursor_row.seq if cursor_row else 0
    pruned_through = cursor_row.pruned_through if cursor_row else 0
    if cursor is None:
        return head, False
    try:
        after = int(cursor)
    except ValueError as exc:
        raise InvalidArgument("That cursor did not come from this server.") from exc
    if not 0 <= after <= head:
        raise InvalidArgument("That cursor did not come from this server.")
    # Rows at or below the watermark are already gone, so there is nothing to
    # skip: just say events may have been missed. The cursor is shared by all
    # of the user's event types, so never jump it (that would skip other types).
    truncated = after < pruned_through
    if max_age_ms is not None:
        floor = now() - timedelta(milliseconds=max_age_ms)
        aged_out = (
            s.query(func.max(EventLog.user_seq))
            .filter(
                EventLog.user_id == user_id,
                EventLog.name == name,
                EventLog.user_seq > after,
                EventLog.created_at < floor,
            )
            .scalar()
        )
        if aged_out is not None:
            after, truncated = int(aged_out), True
    return after, truncated


def events_after(
    s: Session, user_id: str, name: str, after: int, limit: int
) -> list[EventLog]:
    return (
        s.query(EventLog)
        .filter(
            EventLog.user_id == user_id,
            EventLog.name == name,
            EventLog.user_seq > after,
        )
        .order_by(EventLog.user_seq)
        .limit(limit)
        .all()
    )


def _scan(
    s: Session,
    kind: EventType,
    user_id: str,
    arguments: dict[str, Any],
    after: int,
    limit: int,
) -> tuple[list[EventLog], int, bool]:
    """The one read behind poll and push: up to ``limit`` matching events after
    ``after``, the position to continue from, and whether the scan caught up.

    The position moves past rows the arguments filtered out. Once caught up it
    jumps to the user's head, past their other event types. That is safe
    because ``head`` is read before the rows: emits are serialized per user,
    so every seq up to ``head`` had committed and the rows query saw it. Keep
    that order.
    """
    cursor_row = s.get(EventCursor, user_id)
    head = cursor_row.seq if cursor_row else 0
    rows = events_after(s, user_id, kind.name, after, _SCAN_LIMIT)
    matched: list[EventLog] = []
    position = after
    for row in rows:
        if len(matched) == limit:
            break
        position = row.user_seq
        if kind.matches(arguments, row.data):
            matched.append(row)
    caught_up = not rows or (position == rows[-1].user_seq and len(rows) < _SCAN_LIMIT)
    if caught_up:
        position = max(position, head)
    return matched, position, caught_up


def stream_start(
    user_id: str,
    name: str,
    arguments: dict[str, Any],
    cursor: str | None,
    max_age_ms: int | None,
) -> tuple[EventType, dict[str, Any], int, bool]:
    """Validate an ``events/stream`` request: ``(type, arguments, after,
    truncated)``. Raises before the stream opens, never during it."""
    kind = event_type(name)
    arguments = kind.validate_arguments(arguments)
    with use_db_session() as s:
        cursor_row = s.get(EventCursor, user_id)
        after, truncated = start_after(s, cursor_row, user_id, name, cursor, max_age_ms)
    return kind, arguments, after, truncated


def stream_next(
    user_id: str, kind: EventType, arguments: dict[str, Any], after: int
) -> tuple[list[dict[str, Any]], int, bool]:
    """The next batch for an open stream, each event carrying its cursor; the
    position to continue from; and whether the stream has caught up."""
    with use_db_session() as s:
        rows, position, caught_up = _scan(
            s, kind, user_id, arguments, after, _SCAN_LIMIT
        )
        events = [{**occurrence(r), "cursor": str(r.user_seq)} for r in rows]
    return events, position, caught_up


def poll(
    user_id: str,
    name: str,
    arguments: dict[str, Any],
    *,
    cursor: str | None,
    max_age_ms: int | None,
    max_events: int | None,
) -> dict[str, Any]:
    """One ``events/poll``: this principal's events after ``cursor``."""
    kind = event_type(name)
    arguments = kind.validate_arguments(arguments)
    limit = min(max_events or global_config.events.max_poll_events, 1000)
    with use_db_session() as s:
        cursor_row = s.get(EventCursor, user_id)
        after, truncated = start_after(s, cursor_row, user_id, name, cursor, max_age_ms)
        rows, position, caught_up = _scan(s, kind, user_id, arguments, after, limit)
        events = [occurrence(r) for r in rows]
    return {
        "resultType": "complete",
        "events": events,
        "cursor": str(position),
        "truncated": truncated,
        "hasMore": not caught_up,
        "nextPollMs": global_config.events.poll_interval_ms if caught_up else 0,
    }


def prune(older_than: timedelta | None = None) -> int:
    """Drop events past retention, recording each user's ``pruned_through``."""
    retention = older_than or timedelta(days=global_config.events.retention_days)
    cutoff = now() - retention
    # Watermarks first, in their own short transaction (locks taken in user
    # order, so two pruners can't deadlock). The worst case of this order is a
    # brief false "truncated"; the reverse would delete events with no signal.
    with use_db_session() as s:
        reached = (
            s.query(EventLog.user_id, func.max(EventLog.user_seq))
            .filter(EventLog.created_at < cutoff)
            .group_by(EventLog.user_id)
            .order_by(EventLog.user_id)
            .all()
        )
        for user_id, through in reached:
            row = lock_cursor(s, user_id)
            row.pruned_through = max(row.pruned_through, int(through))
        s.commit()
    # Then the deletes, holding no cursor locks, so emits never wait on them.
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
            EventSubscription.expires_at < now() - timedelta(days=1)
        ).delete(synchronize_session=False)
        s.commit()
    return int(removed)


def purge_user(user_id: str, name: str) -> int:
    """Delete a user's events and subscriptions of one type (Gmail disconnect:
    those payloads bank message content).

    Not recorded in ``pruned_through``: that watermark covers all the user's
    event types, and the user asked for this data to go.
    """
    with use_db_session() as s:
        mine = (EventLog.user_id == user_id, EventLog.name == name)
        s.query(WebhookDelivery).filter(
            WebhookDelivery.channel == MCP_CHANNEL,
            WebhookDelivery.event_id.in_(
                s.query(EventLog.event_id).filter(*mine).scalar_subquery()
            ),
        ).delete(synchronize_session=False)
        removed = s.query(EventLog).filter(*mine).delete(synchronize_session=False)
        s.query(EventSubscription).filter(
            EventSubscription.user_id == user_id, EventSubscription.name == name
        ).delete(synchronize_session=False)
        s.commit()
    return int(removed)
