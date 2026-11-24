"""Webhook delivery for MCP Events: subscribe, verify, sign, POST.

    events/subscribe(url, whsec_ secret) ─► SSRF-check url ─► challenge POST
        (unless (principal, url) verified recently) ─► upsert subscription
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
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from common import global_config
from common.token_encryption import require_encryption
from db.engine import use_db_session
from db.models.events import EventLog, EventSubscription
from services import InvalidArgument, ModelFacingError
from services._ssrf_guard import ResolverThreads, build_client, validate_and_pin
from services.events.store import (
    enqueue,
    event_type,
    head_cursor,
    occurrence,
    replay_from,
)
from services.webhooks_svc import validate_webhook_url

_SECRET_PREFIX = "whsec_"  # noqa: S105 - a public format marker, not a secret
_TIMEOUT_S = 5.0
_DEADLINE_S = 15.0
_RESOLVER = ResolverThreads(4)
_REPLAY_LIMIT = 100
# Non-retryable per the spec: the receiver is telling us to stop.
NON_RETRYABLE = frozenset({410, 413})


class CallbackEndpointError(ModelFacingError):
    """The webhook endpoint failed verification (spec code -32027)."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


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
    """One signed POST, pinned to a validated IP (DNS-rebinding safe)."""
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
    deadline = time.monotonic() + _DEADLINE_S
    target = validate_and_pin(
        url, allow_loopback=global_config.is_dev, resolver=_RESOLVER, timeout=_TIMEOUT_S
    )
    with build_client(timeout=_TIMEOUT_S, deadline=deadline) as client:
        return client.post(
            target.url,
            content=raw,
            headers={**headers, **target.headers},
            extensions=target.extensions,
        )


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
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        raise CallbackEndpointError(
            "challenge_failed",
            f"Could not reach the webhook url: {type(exc).__name__}.",
        ) from exc
    if not isinstance(echoed, str) or not hmac.compare_digest(echoed, challenge):
        raise CallbackEndpointError(
            "challenge_failed",
            "The webhook url did not echo the verification challenge (answer 2xx "
            'with {"challenge": "<the nonce>"}).',
        )


def _recently_verified(s: Session, user_id: str, url: str) -> bool:
    window = global_config.events.verification_cache_seconds
    if window <= 0:
        return False
    since = _now() - timedelta(seconds=window)
    return (
        s.query(EventSubscription)
        .filter(
            EventSubscription.user_id == user_id,
            EventSubscription.url == url,
            EventSubscription.verified_at > since,
        )
        .first()
        is not None
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
    if not url.startswith("https://") and not global_config.is_dev:
        raise InvalidArgument("delivery.url must be https.")
    validate_webhook_url(url)
    secret_key(secret)  # format check before any network I/O

    cfg = global_config.events
    ttl = timedelta(
        milliseconds=min(
            ttl_ms or cfg.default_subscription_ttl_seconds * 1000,
            cfg.max_subscription_ttl_seconds * 1000,
        )
    )
    with use_db_session() as s:
        verified = _recently_verified(s, user_id, url)
    if not verified:
        _verify_endpoint(url, secret)  # no DB session held during network I/O

    sub_id = _subscription_id(user_id, url, name, arguments)
    secret_enc = require_encryption().encrypt(secret)
    with use_db_session() as s:
        sub = s.get(EventSubscription, sub_id)
        if sub is None:
            sub = EventSubscription(
                id=sub_id, user_id=user_id, name=name, arguments=arguments, url=url
            )
            s.add(sub)
        sub.secret_enc = secret_enc
        sub.verified_at = _now()
        sub.expires_at = _now() + ttl
        after, truncated = replay_from(s, cursor, max_age_ms)
        if cursor is not None:
            _replay(s, sub, after)
        s.commit()
        return {
            "resultType": "complete",
            "id": sub_id,
            "refreshBefore": _aware(sub.expires_at).isoformat(),
            "cursor": str(max(after, head_cursor(s))),
            "truncated": truncated,
        }


def _replay(s: Session, sub: EventSubscription, after: int) -> None:
    """Enqueue what the subscriber missed since its cursor (bounded)."""
    kind = event_type(sub.name)
    rows = (
        s.query(EventLog)
        .filter(
            EventLog.user_id == sub.user_id,
            EventLog.name == sub.name,
            EventLog.seq > after,
        )
        .order_by(EventLog.seq)
        .limit(_REPLAY_LIMIT)
        .all()
    )
    for row in rows:
        if kind.matches(sub.arguments or {}, row.data):
            enqueue(s, sub.id, row.event_id)


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
    if sub is None or _aware(sub.expires_at) <= _now():
        return None
    event = s.query(EventLog).filter(EventLog.event_id == event_id).first()
    if event is None:
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
        occurrence(event, with_cursor=True),
        message_id=f"msg_{delivery_id}",
        subscription_id=sub.id,
    )
    return resp.status_code
