"""Outbound webhook delivery - drain the outbox, POST signed payloads, retry.

Called by the periodic runner (in-process loop or internal /renew endpoint).
Each tick claims due ``pending`` deliveries, signs and POSTs the event payload
to the subscriber URL, and either marks the row ``succeeded`` or reschedules it
with exponential backoff until ``WEBHOOK_MAX_ATTEMPTS`` is reached.

Multi-replica safety is dialect-aware: on PostgreSQL the claim uses
``FOR UPDATE SKIP LOCKED`` so concurrent runners never grab the same row; on
SQLite (single-replica dev/test) that clause is a no-op and we rely on the
single runner invariant.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import httpx
from loguru import logger as log
from sqlalchemy.orm import Session

from common import global_config
from db.engine import use_db_session
from db.models.webhooks import WebhookDelivery, WebhookEvent, WebhookSubscription
from services.webhooks_svc import (
    DELIVERY_ID_HEADER,
    EVENT_ID_HEADER,
    EVENT_TYPE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    decrypt_secret,
    sign_payload,
)

# Backoff schedule: delay = min(BASE * 2**(attempts-1), CAP).
_BACKOFF_BASE_S = 30
_BACKOFF_CAP_S = 3600
_HTTP_TIMEOUT_S = 10.0
_DEFAULT_BATCH = 20


def _backoff_seconds(attempts: int) -> int:
    """Delay before the next attempt after ``attempts`` failures so far."""
    exp = _BACKOFF_BASE_S * (2 ** max(0, attempts - 1))
    return min(exp, _BACKOFF_CAP_S)


def _claim_due(session: Session, now: datetime, limit: int) -> list[WebhookDelivery]:
    """Select due pending deliveries, locking them on Postgres."""
    query = (
        session.query(WebhookDelivery)
        .filter(
            WebhookDelivery.status == "pending",
            WebhookDelivery.next_attempt_at <= now,
        )
        .order_by(WebhookDelivery.next_attempt_at)
        .limit(limit)
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    return query.all()


def _post(
    url: str, secret: str, delivery: WebhookDelivery, event: WebhookEvent
) -> None:
    """POST the signed payload; raises httpx.HTTPError on transport/HTTP failure."""
    body = json.dumps(
        {
            "id": event.id,
            "type": event.event_type,
            "created_at": event.created_at.isoformat() if event.created_at else None,
            "data": event.payload,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    timestamp = int(time.time())
    signature = sign_payload(secret, timestamp, body)
    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: f"sha256={signature}",
        TIMESTAMP_HEADER: str(timestamp),
        EVENT_ID_HEADER: event.id,
        EVENT_TYPE_HEADER: event.event_type,
        DELIVERY_ID_HEADER: delivery.id,
    }
    with httpx.Client(timeout=_HTTP_TIMEOUT_S) as client:
        resp = client.post(url, content=body, headers=headers)
    resp.raise_for_status()


def _process(session: Session, delivery: WebhookDelivery) -> str:
    """Attempt one delivery, mutating its row. Returns 'sent'|'retry'|'failed'|'dropped'."""
    sub = session.get(WebhookSubscription, delivery.subscription_id)
    event = session.get(WebhookEvent, delivery.event_id)
    if sub is None or event is None or not sub.active:
        # Subscriber gone or deactivated after enqueue: stop trying.
        delivery.status = "failed"
        delivery.last_error = "subscription inactive or missing"
        return "dropped"

    delivery.attempts += 1
    try:
        _post(sub.url, decrypt_secret(sub.secret_enc), delivery, event)
    except httpx.HTTPError as exc:
        delivery.last_error = f"{type(exc).__name__}: {exc}"[:1000]
        if delivery.attempts >= global_config.WEBHOOK_MAX_ATTEMPTS:
            delivery.status = "failed"
            log.warning(
                "webhook delivery {} gave up after {} attempts: {}",
                delivery.id,
                delivery.attempts,
                delivery.last_error,
            )
            return "failed"
        delivery.next_attempt_at = datetime.now(UTC) + timedelta(
            seconds=_backoff_seconds(delivery.attempts)
        )
        return "retry"

    delivery.status = "succeeded"
    delivery.last_error = None
    return "sent"


def drain_due_deliveries(limit: int = _DEFAULT_BATCH) -> dict[str, int]:
    """Claim and attempt up to ``limit`` due deliveries. Returns outcome counts."""
    counts = {"sent": 0, "retry": 0, "failed": 0, "dropped": 0}
    with use_db_session() as session:
        now = datetime.now(UTC)
        due = _claim_due(session, now, limit)
        for delivery in due:
            outcome = _process(session, delivery)
            counts[outcome] += 1
        session.commit()
    if any(counts.values()):
        log.debug("drain_due_deliveries: {}", counts)
    return counts


# ---------------------------------------------------------------------------
# Cleanup (called opportunistically by the runner)
# ---------------------------------------------------------------------------


def cleanup_delivered(older_than_days: int = 7) -> int:
    """Delete succeeded deliveries older than the cutoff. Returns rows removed."""
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    with use_db_session() as session:
        removed = (
            session.query(WebhookDelivery)
            .filter(
                WebhookDelivery.status == "succeeded",
                WebhookDelivery.updated_at < cutoff,
            )
            .delete(synchronize_session=False)
        )
        session.commit()
    return int(removed)
