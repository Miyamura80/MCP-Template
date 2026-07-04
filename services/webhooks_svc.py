"""Outbound webhook services - subscribe / list / unsubscribe / rotate + enqueue.

Pure, transport-agnostic business logic. When a connected Gmail account
receives new mail, ``enqueue_event`` records a :class:`WebhookEvent` and fans
out one pending :class:`WebhookDelivery` per matching active subscription.
Draining that outbox (signing + POSTing + retry) lives in the sibling
``webhook_delivery_svc`` module.

Signing mirrors Stripe: ``X-Webhook-Signature: sha256=<hex>`` over
``{timestamp}.{body}`` with the subscription's per-endpoint secret, plus an
``X-Webhook-Timestamp`` header so subscribers can reject replays. Secrets are
Fernet-encrypted at rest with the same backend used for Google refresh tokens
and returned in cleartext only at create / rotate time.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from typing import Any

from loguru import logger as log
from sqlalchemy.orm import Session

from common.token_encryption import require_encryption
from db.engine import use_db_session
from db.models.webhooks import WebhookDelivery, WebhookEvent, WebhookSubscription
from models.webhooks import (
    WebhookListInput,
    WebhookListResult,
    WebhookRotateSecretInput,
    WebhookRotateSecretResult,
    WebhookSubscribeInput,
    WebhookSubscribeResult,
    WebhookSubscriptionView,
    WebhookUnsubscribeInput,
    WebhookUnsubscribeResult,
)
from services import service

# Header names on delivered POSTs (kept here so the delivery module imports one).
SIGNATURE_HEADER = "X-Webhook-Signature"
TIMESTAMP_HEADER = "X-Webhook-Timestamp"
EVENT_ID_HEADER = "X-Webhook-Event-Id"
EVENT_TYPE_HEADER = "X-Webhook-Event-Type"
DELIVERY_ID_HEADER = "X-Webhook-Delivery-Id"

_SECRET_PREFIX = "whsec_"  # noqa: S105 - not a secret, a public prefix marker


# ---------------------------------------------------------------------------
# ID / secret / signing helpers
# ---------------------------------------------------------------------------


def _new_id() -> str:
    """32-char random hex id; fits the String(64) primary keys."""
    return secrets.token_hex(16)


def _new_secret() -> str:
    return _SECRET_PREFIX + secrets.token_urlsafe(32)


def sign_payload(secret: str, timestamp: int, body: bytes) -> str:
    """Hex HMAC-SHA256 over ``{timestamp}.{body}`` (Stripe-style, replay-safe)."""
    mac = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode() + body,
        hashlib.sha256,
    )
    return mac.hexdigest()


def _encrypt_secret(secret: str) -> tuple[bytes, str]:
    enc = require_encryption()
    return enc.encrypt(secret), enc.key_id


def decrypt_secret(ciphertext: bytes) -> str:
    """Decrypt a stored signing secret (used by the delivery module)."""
    return require_encryption().decrypt(ciphertext)


# ---------------------------------------------------------------------------
# Services (CRUD over subscriptions)
# ---------------------------------------------------------------------------


@service(
    name="webhook_subscribe",
    description="Register an HTTPS endpoint to receive signed webhook events",
    input_model=WebhookSubscribeInput,
    output_model=WebhookSubscribeResult,
)
def webhook_subscribe(input: WebhookSubscribeInput) -> WebhookSubscribeResult:
    """Create a subscription and return its one-time signing secret."""
    if not input.url.lower().startswith(("http://", "https://")):
        raise ValueError("Webhook url must be an http(s) URL")

    secret = _new_secret()
    secret_enc, key_id = _encrypt_secret(secret)
    sub_id = _new_id()
    event_types = input.event_types or None

    with use_db_session() as session:
        session.add(
            WebhookSubscription(
                id=sub_id,
                user_id=input.user_id,
                url=input.url,
                secret_enc=secret_enc,
                key_id=key_id,
                event_types=event_types,
                active=True,
            )
        )
        session.commit()

    return WebhookSubscribeResult(
        id=sub_id,
        url=input.url,
        event_types=event_types,
        active=True,
        secret=secret,
    )


@service(
    name="webhook_list",
    description="List the caller's webhook subscriptions (secrets are never returned)",
    input_model=WebhookListInput,
    output_model=WebhookListResult,
)
def webhook_list(input: WebhookListInput) -> WebhookListResult:
    with use_db_session() as session:
        rows = (
            session.query(WebhookSubscription)
            .filter(WebhookSubscription.user_id == input.user_id)
            .order_by(WebhookSubscription.created_at.desc())
            .all()
        )
        views = [
            WebhookSubscriptionView(
                id=r.id,
                url=r.url,
                event_types=r.event_types,
                active=r.active,
                created_at=r.created_at,
            )
            for r in rows
        ]
    return WebhookListResult(subscriptions=views)


@service(
    name="webhook_unsubscribe",
    description="Deactivate a webhook subscription so it stops receiving events",
    input_model=WebhookUnsubscribeInput,
    output_model=WebhookUnsubscribeResult,
)
def webhook_unsubscribe(
    input: WebhookUnsubscribeInput,
) -> WebhookUnsubscribeResult:
    with use_db_session() as session:
        row = (
            session.query(WebhookSubscription)
            .filter(
                WebhookSubscription.id == input.subscription_id,
                WebhookSubscription.user_id == input.user_id,
            )
            .one_or_none()
        )
        if row is None or not row.active:
            return WebhookUnsubscribeResult(unsubscribed=False)
        row.active = False
        session.commit()
    return WebhookUnsubscribeResult(unsubscribed=True)


@service(
    name="webhook_rotate_secret",
    description="Issue a new signing secret for a subscription (invalidates the old one)",
    input_model=WebhookRotateSecretInput,
    output_model=WebhookRotateSecretResult,
)
def webhook_rotate_secret(
    input: WebhookRotateSecretInput,
) -> WebhookRotateSecretResult:
    secret = _new_secret()
    secret_enc, key_id = _encrypt_secret(secret)
    with use_db_session() as session:
        row = (
            session.query(WebhookSubscription)
            .filter(
                WebhookSubscription.id == input.subscription_id,
                WebhookSubscription.user_id == input.user_id,
            )
            .one_or_none()
        )
        if row is None:
            raise ValueError("Subscription not found")
        row.secret_enc = secret_enc
        row.key_id = key_id
        session.commit()
    return WebhookRotateSecretResult(id=input.subscription_id, secret=secret)


# ---------------------------------------------------------------------------
# Enqueue (fan-out) - called by the Gmail push receiver within its own session
# ---------------------------------------------------------------------------


def enqueue_event(
    session: Session,
    *,
    user_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> str | None:
    """Record an event and fan out one pending delivery per matching sub.

    A subscription matches when it is active and either declares no
    ``event_types`` filter or lists ``event_type`` explicitly. Returns the
    new event id, or ``None`` when the user has no matching subscription (no
    event row is written in that case). Flushes but does not commit - the
    caller owns the surrounding transaction.
    """
    subs = (
        session.query(WebhookSubscription)
        .filter(
            WebhookSubscription.user_id == user_id,
            WebhookSubscription.active.is_(True),
        )
        .all()
    )
    matching = [s for s in subs if not s.event_types or event_type in s.event_types]
    if not matching:
        return None

    now = datetime.now(UTC)
    event_id = _new_id()
    session.add(
        WebhookEvent(
            id=event_id,
            user_id=user_id,
            event_type=event_type,
            payload=payload,
        )
    )
    for sub in matching:
        session.add(
            WebhookDelivery(
                id=_new_id(),
                event_id=event_id,
                subscription_id=sub.id,
                status="pending",
                attempts=0,
                next_attempt_at=now,
            )
        )
    session.flush()
    log.debug(
        "enqueued webhook event {} ({}) -> {} deliveries",
        event_id,
        event_type,
        len(matching),
    )
    return event_id
