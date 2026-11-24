"""Webhook delivery for MCP Events: subscribe, verify, sign, POST.

    events/subscribe(url, whsec_ secret) ─► SSRF-check url ─► challenge POST
        (unless (principal, url) passed one recently) ─► upsert subscription
        under the principal's cursor lock (so no emit slips between replay
        and the subscription becoming visible)
    emit() ─► outbox row ─► drain (shared with legacy webhooks) ─► post_event()

Signing is the Standard Webhooks profile the spec requires: headers
``webhook-id``, ``webhook-timestamp``, ``webhook-signature: v1,<base64
HMAC-SHA256>`` over ``{id}.{timestamp}.{body}``, keyed by the base64 bytes
after ``whsec_``. The client generates the secret; the server only stores it
(Fernet-encrypted) and never returns it.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from common import global_config
from common.token_encryption import require_encryption
from db.engine import use_db_session
from db.models.events import EventLog, EventSubscription
from services import InvalidArgument, ModelFacingError
from services._ssrf_guard import ResolverBusyError
from services.events.store import (
    aware,
    enqueue,
    event_type,
    events_after,
    lock_cursor,
    now,
    occurrence,
    start_after,
)
from services.webhooks_svc import pinned_post, validate_webhook_url

_SECRET_PREFIX = "whsec_"  # noqa: S105 - a public format marker, not a secret
_TIMEOUT_S = 10.0
_DEADLINE_S = 30.0
_REPLAY_LIMIT = 100
# Non-retryable per the spec: the receiver is telling us to stop.
NON_RETRYABLE = frozenset({410, 413})


class CallbackEndpointError(ModelFacingError):
    """The webhook endpoint failed verification (spec code -32027)."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


def secret_key(secret: str) -> bytes:
    """The HMAC key in a ``whsec_<base64 of 24-64 random bytes>`` secret."""
    if not secret.startswith(_SECRET_PREFIX):
        raise InvalidArgument("delivery.secret must start with whsec_.")
    try:
        key = base64.b64decode(secret.removeprefix(_SECRET_PREFIX), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidArgument("delivery.secret must be whsec_ + base64.") from exc
    if not 24 <= len(key) <= 64:
        raise InvalidArgument("delivery.secret must encode 24 to 64 random bytes.")
    return key


def sign(secret: str, message_id: str, timestamp: int, body: bytes) -> str:
    signed = f"{message_id}.{timestamp}.".encode() + body
    digest = hmac.new(secret_key(secret), signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def _subscription_id(
    user_id: str, url: str, name: str, arguments: dict[str, Any]
) -> str:
    key = json.dumps([user_id, url, name, arguments], sort_keys=True)
    return "sub_" + hashlib.sha256(key.encode()).hexdigest()[:32]


def _post(
    url: str,
    secret: str,
    body: dict[str, Any],
    *,
    message_id: str,
    subscription_id: str | None,
) -> httpx.Response:
    raw = json.dumps(body, separators=(",", ":")).encode()
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "webhook-id": message_id,
        "webhook-timestamp": str(timestamp),
        "webhook-signature": sign(secret, message_id, timestamp, raw),
    }
    if subscription_id is not None:
        headers["X-MCP-Subscription-Id"] = subscription_id
    return pinned_post(url, raw, headers, timeout=_TIMEOUT_S, deadline_s=_DEADLINE_S)


def _verify_endpoint(url: str, secret: str) -> None:
    """The spec's challenge handshake: the endpoint must echo our nonce."""
    challenge = secrets.token_urlsafe(24)
    try:
        resp = _post(
            url,
            secret,
            {"type": "verification", "challenge": challenge},
            message_id=f"msg_verification_{secrets.token_urlsafe(12)}",
            subscription_id=None,
        )
        echoed = resp.json().get("challenge") if resp.is_success else None
    except (httpx.HTTPError, ValueError, AttributeError, ResolverBusyError) as exc:
        # ValueError covers an SSRF reject at pin time and a non-JSON answer.
        raise CallbackEndpointError(
            "challenge_failed",
            f"Could not verify the webhook url ({type(exc).__name__}).",
        ) from exc
    if not isinstance(echoed, str) or not hmac.compare_digest(echoed, challenge):
        raise CallbackEndpointError(
            "challenge_failed",
            "The webhook url did not echo the verification challenge (answer 2xx "
            'with {"challenge": "<the nonce>"}).',
        )


def _last_verified(s: Session, user_id: str, url: str) -> datetime | None:
    """When (principal, url) last passed a challenge, if inside the cache window."""
    window = global_config.events.verification_cache_seconds
    if window <= 0:
        return None
    since = now() - timedelta(seconds=window)
    row = (
        s.query(EventSubscription.verified_at)
        .filter(
            EventSubscription.user_id == user_id,
            EventSubscription.url == url,
            EventSubscription.verified_at > since,
        )
        .order_by(EventSubscription.verified_at.desc())
        .first()
    )
    return row[0] if row else None


def _at_cap(s: Session, user_id: str) -> bool:
    count = s.query(EventSubscription).filter_by(user_id=user_id).count()
    return count >= global_config.events.max_subscriptions_per_user


def _cap_error() -> InvalidArgument:
    cap = global_config.events.max_subscriptions_per_user
    return InvalidArgument(
        f"At most {cap} event subscriptions per user; unsubscribe one first."
    )


def subscribe(
    user_id: str,
    name: str,
    arguments: dict[str, Any],
    *,
    url: str,
    secret: str,
    cursor: str | None,
    max_age_ms: int | None,
    ttl_ms: int | None,
) -> dict[str, Any]:
    """Create or refresh the subscription keyed (principal, url, name, args)."""
    kind = event_type(name)
    arguments = kind.validate_arguments(arguments)
    validate_webhook_url(url)  # https, and never a private address (dev: loopback)
    secret_key(secret)  # format check before any network I/O
    cfg = global_config.events
    ttl = timedelta(
        milliseconds=min(
            ttl_ms or cfg.default_subscription_ttl_seconds * 1000,
            cfg.max_subscription_ttl_seconds * 1000,
        )
    )
    sub_id = _subscription_id(user_id, url, name, arguments)

    with use_db_session() as s:
        verified_at = _last_verified(s, user_id, url)
        # Early check, so a capped user doesn't get a challenge POST.
        if s.get(EventSubscription, sub_id) is None and _at_cap(s, user_id):
            raise _cap_error()
    if verified_at is None:
        _verify_endpoint(url, secret)  # no DB session held during network I/O
        verified_at = now()

    secret_enc = require_encryption().encrypt(secret)
    with use_db_session() as s:
        cursor_row = lock_cursor(s, user_id)  # held until commit: see module doc
        sub = s.get(EventSubscription, sub_id)
        if sub is None and _at_cap(s, user_id):  # re-checked under the lock
            raise _cap_error()
        if sub is None:
            sub = EventSubscription(
                id=sub_id, user_id=user_id, name=name, arguments=arguments, url=url
            )
            s.add(sub)
        sub.secret_enc = secret_enc
        # Only a challenge renews verification, so a daily refresh doesn't
        # keep an endpoint trusted forever.
        sub.verified_at = verified_at
        sub.expires_at = now() + ttl
        after, truncated = start_after(s, cursor_row, user_id, name, cursor, max_age_ms)
        if cursor is not None:
            truncated = _replay(s, sub, after) or truncated
        s.commit()
        return {
            "resultType": "complete",
            "id": sub_id,
            "refreshBefore": aware(sub.expires_at).isoformat(),
            "cursor": str(cursor_row.seq),
            "truncated": truncated,
        }


def _replay(s: Session, sub: EventSubscription, after: int) -> bool:
    """Enqueue what the subscriber missed since its cursor. True when the
    replay was cut at its cap, so the caller reports the gap."""
    kind = event_type(sub.name)
    rows = events_after(s, sub.user_id, sub.name, after, _REPLAY_LIMIT + 1)
    for row in rows[:_REPLAY_LIMIT]:
        if kind.matches(sub.arguments or {}, row.data):
            enqueue(s, sub.id, row.event_id)
    return len(rows) > _REPLAY_LIMIT


def unsubscribe(user_id: str, name: str, arguments: dict[str, Any], url: str) -> None:
    kind = event_type(name)
    sub_id = _subscription_id(user_id, url, name, kind.validate_arguments(arguments))
    with use_db_session() as s:
        s.query(EventSubscription).filter(
            EventSubscription.id == sub_id, EventSubscription.user_id == user_id
        ).delete(synchronize_session=False)
        s.commit()


def deliverable(
    s: Session, subscription_id: str, event_id: str
) -> tuple[EventSubscription, EventLog] | None:
    """The live subscription and event for one outbox row, or ``None``."""
    sub = s.get(EventSubscription, subscription_id)
    if sub is None or aware(sub.expires_at) <= now():
        return None
    event = s.query(EventLog).filter(EventLog.event_id == event_id).first()
    # Every enqueue keeps these the same user; refuse anything else outright.
    if event is None or event.user_id != sub.user_id:
        return None
    return sub, event


def post_event(sub: EventSubscription, event: EventLog, delivery_id: str) -> int:
    """POST one occurrence. Returns the HTTP status; raises on transport errors.

    The ``webhook-id`` is stable per outbox row, so a retried delivery dedups
    on the receiver; timestamp and signature are regenerated each attempt.
    """
    secret = require_encryption().decrypt(sub.secret_enc)
    resp = _post(
        sub.url,
        secret,
        {**occurrence(event), "cursor": str(event.user_seq)},
        message_id=f"msg_{delivery_id}",
        subscription_id=sub.id,
    )
    return resp.status_code
