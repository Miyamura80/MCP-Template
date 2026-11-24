"""Outbound webhook delivery - drain the outbox, POST signed payloads, retry.

Called by the periodic runner (in-process loop or internal /renew endpoint).
Each tick atomically *claims* a batch of due deliveries (flipping them to
``sending`` and committing so no row locks are held during network I/O), then
processes each in its own short transaction: POST the signed payload and mark
the row ``succeeded``, or reschedule with exponential backoff (``pending``)
until ``WEBHOOK_MAX_ATTEMPTS`` is reached (``failed``).

Two invariants make this safe:

* **Per-row isolation.** Each delivery commits independently, and ``_process``
  never propagates - any failure (HTTP, an undecryptable secret after key
  rotation, a serialization error) is recorded on that row and follows the
  bounded retry/give-up path. One poison row can neither wedge the outbox nor
  cause an already-succeeded sibling to be re-sent.
* **Lock-free network I/O.** The claim uses ``FOR UPDATE SKIP LOCKED`` on
  Postgres so concurrent runners never grab the same row, but the lock is
  released at claim-commit time - the blocking POSTs run with no row locks and
  no open transaction. A runner that crashes mid-send leaves rows in
  ``sending``; the next claim reclaims any ``sending`` row older than the lease.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger as log
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from common import global_config
from db.engine import use_db_session
from db.models.webhooks import WebhookDelivery, WebhookEvent, WebhookSubscription
from services.events.store import MCP_CHANNEL
from services.events.webhooks import NON_RETRYABLE, deliverable, post_event
from services.webhooks_svc import (
    DELIVERY_ID_HEADER,
    EVENT_ID_HEADER,
    EVENT_TYPE_HEADER,
    OUTBOUND_RESOLVER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    decrypt_secret,
    pinned_post,
    sign_payload,
)

# Backoff schedule: delay = min(BASE * 2**(attempts-1), CAP).
_BACKOFF_BASE_S = 30
_BACKOFF_CAP_S = 3600
# MCP Events retry delays: attempts at about t = 0, 1, 4 and 11 minutes, inside
# the spec's "3 to 5 attempts within 10 to 15 minutes".
_MCP_SCHEDULE_S = (60, 180, 420)
_HTTP_TIMEOUT_S = 10.0
# Wall-clock ceiling on one delivery POST: the per-operation timeout alone lets a
# subscriber that trickles its response hold the runner indefinitely.
_DELIVERY_DEADLINE_S = 30.0
# Delivery DNS runs on these threads; a hung lookup holds one (and only one
# per host) until the system resolver gives up, never the runner itself.
_RESOLVER = OUTBOUND_RESOLVER
_DEFAULT_BATCH = 20
# A claimed ("sending") row left behind by a crashed runner is reclaimable
# after this many seconds so deliveries never get stranded.
_CLAIM_LEASE_S = 300


def _backoff_seconds(attempts: int) -> int:
    """Delay before the next attempt after ``attempts`` failures so far."""
    exp = _BACKOFF_BASE_S * (2 ** max(0, attempts - 1))
    return min(exp, _BACKOFF_CAP_S)


def _claim_batch(session: Session, now: datetime, limit: int) -> list[str]:
    """Atomically claim up to ``limit`` due deliveries; return their ids.

    Selects rows that are ``pending`` and due, plus any ``sending`` row whose
    lease has expired (crashed-runner recovery), flips them to ``sending``, and
    commits - releasing the ``FOR UPDATE SKIP LOCKED`` locks (Postgres) before
    any HTTP I/O happens. The select+update run in one transaction so two
    replicas can never claim the same row.
    """
    lease_cutoff = now - timedelta(seconds=_CLAIM_LEASE_S)
    query = (
        session.query(WebhookDelivery.id)
        .filter(
            or_(
                and_(
                    WebhookDelivery.status == "pending",
                    WebhookDelivery.next_attempt_at <= now,
                ),
                and_(
                    WebhookDelivery.status == "sending",
                    WebhookDelivery.updated_at < lease_cutoff,
                ),
            )
        )
        .order_by(WebhookDelivery.next_attempt_at)
        .limit(limit)
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)

    ids = [row[0] for row in query.all()]
    if ids:
        session.query(WebhookDelivery).filter(WebhookDelivery.id.in_(ids)).update(
            {WebhookDelivery.status: "sending", WebhookDelivery.updated_at: now},
            synchronize_session=False,
        )
        session.commit()
    return ids


def _post(url: str, secret: str, delivery: WebhookDelivery, event: WebhookEvent) -> int:
    """POST the signed payload; returns the status. Raises on transport failure
    or SSRF reject.

    The subscriber URL was validated at subscribe time, but DNS can be flipped
    between then and now (rebinding). We therefore re-resolve here and PIN the
    connection to a validated global IP - the original hostname is preserved via
    the ``Host`` header + TLS SNI, so the socket lands on the IP that just passed
    the allowlist and never on a rebind target like 169.254.169.254 or an
    RFC1918 host. ``validate_and_pin`` raises :class:`SsrfError` (a ValueError)
    if the host now resolves to a non-global address; that propagates to
    ``_process``'s defensive boundary, which records it as a delivery failure so
    the POST is refused, not silently retried against internal infrastructure.
    """
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
    # follow_redirects=False inside: a 3xx (e.g. Location: http://169.254.169.254/)
    # comes back as a non-2xx status and is retried, never chased inward.
    return pinned_post(
        url,
        body,
        headers,
        timeout=_HTTP_TIMEOUT_S,
        deadline_s=_DELIVERY_DEADLINE_S,
        resolver=_RESOLVER,
    ).status_code


@dataclass(frozen=True)
class RetryPolicy:
    """How a channel retries: attempts, the delay after the n-th failure, and
    statuses that mean "stop" rather than "try again"."""

    max_attempts: Callable[[], int]  # read at use, so config/test patches apply
    delay_s: Callable[[int], int]
    non_retryable: frozenset[int] = frozenset()


def _mcp_delay(attempts: int) -> int:
    return _MCP_SCHEDULE_S[min(attempts, len(_MCP_SCHEDULE_S)) - 1]


@dataclass(frozen=True)
class Channel:
    """One kind of outbox row. ``load`` returns the zero-argument sender for a
    row (raising inside it is a delivery failure), or a reason to drop it."""

    load: Callable[[Session, WebhookDelivery], Callable[[], int] | str]
    policy: RetryPolicy


def _load_legacy(
    session: Session, delivery: WebhookDelivery
) -> Callable[[], int] | str:
    sub = session.get(WebhookSubscription, delivery.subscription_id)
    event = session.get(WebhookEvent, delivery.event_id)
    if sub is None or event is None or not sub.active:
        return "subscription inactive or missing"
    return lambda: _post(sub.url, decrypt_secret(sub.secret_enc), delivery, event)


def _load_mcp_event(
    session: Session, delivery: WebhookDelivery
) -> Callable[[], int] | str:
    found = deliverable(session, delivery.subscription_id, delivery.event_id)
    if found is None:
        return "subscription expired or missing, or event pruned"
    sub, event = found
    return lambda: post_event(sub, event, delivery.id)


_CHANNELS: dict[str, Channel] = {
    "webhook": Channel(
        _load_legacy,
        RetryPolicy(lambda: global_config.WEBHOOK_MAX_ATTEMPTS, _backoff_seconds),
    ),
    # The spec: 3 to 5 attempts within 10 to 15 minutes; 410/413 mean stop.
    MCP_CHANNEL: Channel(
        _load_mcp_event,
        RetryPolicy(
            lambda: global_config.events.webhook_max_attempts,
            _mcp_delay,
            NON_RETRYABLE,
        ),
    ),
}


def _record_failure(delivery: WebhookDelivery, error: str, policy: RetryPolicy) -> str:
    """Mark a delivery failed (at max attempts) or reschedule with backoff."""
    delivery.last_error = error[:1000]
    if delivery.attempts >= policy.max_attempts():
        delivery.status = "failed"
        log.warning(
            "webhook delivery {} gave up after {} attempts: {}",
            delivery.id,
            delivery.attempts,
            delivery.last_error,
        )
        return "failed"
    # Back to pending so the next claim re-selects it once the backoff elapses.
    delivery.status = "pending"
    delivery.next_attempt_at = datetime.now(UTC) + timedelta(
        seconds=policy.delay_s(delivery.attempts)
    )
    return "retry"


def _process(session: Session, delivery: WebhookDelivery) -> str:
    """Attempt one delivery, mutating its row. Never raises.

    Returns 'sent' | 'retry' | 'failed' | 'dropped'.
    """
    channel = _CHANNELS.get(delivery.channel)
    send = channel.load(session, delivery) if channel else "unknown channel"
    if isinstance(send, str) or channel is None:
        # Subscriber gone, expired or deactivated after enqueue: stop trying.
        delivery.status = "failed"
        delivery.last_error = send if isinstance(send, str) else "unknown channel"
        return "dropped"

    delivery.attempts += 1
    try:
        status = send()
    except Exception as exc:  # noqa: BLE001
        # Defensive per-delivery boundary: a transport error, an SSRF reject at
        # pin time, an undecryptable secret after key rotation or a
        # non-serializable payload must not escape and abort the runner or roll
        # back sibling deliveries. Record it and follow the same bounded
        # backoff/give-up path so this row cannot wedge the outbox.
        return _record_failure(delivery, f"{type(exc).__name__}: {exc}", channel.policy)
    if 200 <= status < 300:
        delivery.status = "succeeded"
        delivery.last_error = None
        return "sent"
    if status in channel.policy.non_retryable:
        delivery.status = "failed"
        delivery.last_error = f"HTTP {status} (receiver asked us to stop)"
        return "failed"
    return _record_failure(delivery, f"HTTP {status}", channel.policy)


def _process_one(delivery_id: str) -> str:
    """Process a single claimed delivery in its own transaction."""
    with use_db_session() as session:
        delivery = session.get(WebhookDelivery, delivery_id)
        if delivery is None:
            return "dropped"
        outcome = _process(session, delivery)
        session.commit()
        return outcome


def drain_due_deliveries(limit: int = _DEFAULT_BATCH) -> dict[str, int]:
    """Claim and attempt up to ``limit`` due deliveries. Returns outcome counts."""
    counts = {"sent": 0, "retry": 0, "failed": 0, "dropped": 0}
    with use_db_session() as session:
        claimed = _claim_batch(session, datetime.now(UTC), limit)
    # Locks are released; POST each claimed row in its own short transaction so
    # one failure can neither block nor roll back the others.
    for delivery_id in claimed:
        counts[_process_one(delivery_id)] += 1
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
