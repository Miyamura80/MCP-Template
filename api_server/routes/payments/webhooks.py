"""Stripe webhook handler with dual-secret verification."""

import asyncio
import random
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from loguru import logger as log
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError

from api_server.billing.stripe_config import ensure_stripe, get_webhook_secret
from db.engine import use_db_session
from db.models.processed_stripe_events import ProcessedStripeEvent
from db.models.subscription_types import (
    PaymentStatus,
    SubscriptionStatus,
    SubscriptionTier,
)
from db.models.user_subscriptions import UserSubscription

router = APIRouter(prefix="/api/v1/billing/webhook", tags=["billing"])


async def _dispatch_event(event_type: str, data: dict, event_id: str) -> None:
    """Route a webhook event to the appropriate sync handler."""
    if event_type == "customer.subscription.created":
        await asyncio.to_thread(
            _handle_subscription_created, data, event_id, event_type
        )
    elif event_type == "customer.subscription.updated":
        await asyncio.to_thread(
            _handle_subscription_updated, data, event_id, event_type
        )
    elif event_type == "customer.subscription.deleted":
        await asyncio.to_thread(
            _handle_subscription_deleted, data, event_id, event_type
        )
    elif event_type == "invoice.payment_failed":
        await asyncio.to_thread(_handle_payment_failed, data, event_id, event_type)
    elif event_type == "invoice.payment_succeeded":
        await asyncio.to_thread(_handle_payment_succeeded, data, event_id, event_type)
    else:
        log.debug("Unhandled webhook event: {}", event_type)


def _try_construct_event(payload: bytes, sig_header: str):
    """Try to verify with primary secret, then fallback to test secret."""
    import stripe

    secret = get_webhook_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")

    try:
        return stripe.Webhook.construct_event(payload, sig_header, secret)
    except stripe.SignatureVerificationError:
        # Only try fallback secret in non-production environments.
        # In production, accepting a test-signed webhook would be a
        # security risk (test-mode dashboard access could inject events).
        from common import global_config

        if global_config.DEV_ENV == "prod":
            raise

        fallback = (
            global_config.STRIPE_TEST_WEBHOOK_SECRET
            if secret == global_config.STRIPE_WEBHOOK_SECRET
            else global_config.STRIPE_WEBHOOK_SECRET
        )
        if fallback and fallback != secret:
            return stripe.Webhook.construct_event(payload, sig_header, fallback)
        raise


@router.post("/stripe")
async def stripe_webhook(request: Request):
    """Handle incoming Stripe webhook events."""
    if not ensure_stripe():
        raise HTTPException(status_code=503, detail="Billing not configured")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = _try_construct_event(payload, sig_header)
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Webhook signature verification failed: {}", exc)
        raise HTTPException(status_code=400, detail="Invalid signature") from exc

    event_id = event["id"]
    event_type = event["type"]
    data = event["data"]["object"]

    # Dispatch to sync handlers via asyncio.to_thread to avoid blocking
    # the event loop during synchronous SQLAlchemy DB operations.
    # _CustomerNotFoundError is a domain exception raised by handlers;
    # convert it to an HTTP 500 here so Stripe retries the webhook.
    try:
        await _dispatch_event(event_type, data, event_id)
    except _CustomerNotFoundError:
        raise HTTPException(
            status_code=500, detail="Customer not found, will retry"
        ) from None

    # Probabilistic cleanup of old processed events (1% of requests)
    if random.random() < 0.01:  # noqa: S311
        await asyncio.to_thread(_cleanup_old_events)

    return {"received": True}


_EVENT_RETENTION = timedelta(days=7)


def _cleanup_old_events() -> None:
    """Delete processed_stripe_events older than 7 days.

    Both webhook dedup records and ``meter:`` metering idempotency keys
    share the same 7-day retention window.  This is sufficient for
    metering callers since Stripe itself expires idempotency keys after
    24 hours.
    """
    try:
        cutoff = datetime.now(UTC) - _EVENT_RETENTION
        with use_db_session() as session:
            result = session.execute(
                delete(ProcessedStripeEvent).where(
                    ProcessedStripeEvent.processed_at < cutoff,
                )
            )
            session.commit()
            if result.rowcount:
                log.info("Cleaned up {} old processed stripe events", result.rowcount)
    except Exception:
        log.debug("Failed to clean up old processed stripe events")


def _find_subscription_by_customer(
    session, customer_id: str
) -> UserSubscription | None:
    return (
        session.query(UserSubscription)
        .filter_by(stripe_customer_id=customer_id)
        .first()
    )


def _mark_event_processed(session, event_id: str, event_type: str) -> bool:
    """Try to insert event into processed_stripe_events table.

    Returns True if the event was newly inserted (not a duplicate).
    Returns False if the event was already processed (IntegrityError on PK).
    """
    try:
        session.add(ProcessedStripeEvent(event_id=event_id, event_type=event_type))
        session.flush()
        return True
    except IntegrityError:
        session.rollback()
        return False


_STRIPE_STATUS_MAP = {
    "trialing": SubscriptionStatus.TRIALING.value,
    "active": SubscriptionStatus.ACTIVE.value,
    "incomplete": SubscriptionStatus.INCOMPLETE.value,
    "incomplete_expired": SubscriptionStatus.CANCELED.value,
    "past_due": SubscriptionStatus.PAST_DUE.value,
    "canceled": SubscriptionStatus.CANCELED.value,
    "unpaid": SubscriptionStatus.PAST_DUE.value,
    "paused": SubscriptionStatus.ACTIVE.value,  # Paused is voluntary, not delinquent
}


def _map_stripe_status(data: dict) -> tuple[str, bool]:
    """Map Stripe subscription status to local enum and is_active flag.

    **Policy decision**: ``paused`` subscriptions retain active entitlements.
    Stripe pauses collection voluntarily (e.g., customer request), so the
    user is not delinquent.  If paused users should lose paid-tier access,
    change the mapping to ``SubscriptionStatus.PAST_DUE`` and remove
    ``"paused"`` from the ``is_active`` set below.
    """
    stripe_status = data.get("status", "active")
    local_status = _STRIPE_STATUS_MAP.get(
        stripe_status, SubscriptionStatus.PAST_DUE.value
    )
    is_active = stripe_status in ("trialing", "active", "paused")
    return local_status, is_active


class _CustomerNotFoundError(Exception):
    """Raised when a webhook references an unknown customer (triggers retry)."""


def _handle_subscription_created(data: dict, event_id: str, event_type: str) -> None:
    customer_id = data.get("customer")
    if not customer_id:
        return

    with use_db_session() as session:
        if not _mark_event_processed(session, event_id, event_type):
            log.debug("Duplicate event {} for customer {}", event_id, customer_id)
            return

        sub = _find_subscription_by_customer(session, customer_id)
        if not sub:
            log.error(
                "Received subscription.created for unknown customer {}; will retry",
                customer_id,
            )
            raise _CustomerNotFoundError(customer_id)

        local_status, is_active = _map_stripe_status(data)
        sub.stripe_subscription_id = data.get("id")
        # Currently only the PLUS tier goes through Stripe checkout.
        # If additional tiers are added, resolve via Stripe price/product
        # metadata: data["items"]["data"][0]["price"]["id"].
        sub.subscription_tier = SubscriptionTier.PLUS.value
        sub.subscription_status = local_status
        sub.is_active = is_active

        current_period = data.get("current_period_start")
        if current_period:
            sub.current_period_start = datetime.fromtimestamp(current_period, tz=UTC)
        period_end = data.get("current_period_end")
        if period_end:
            sub.current_period_end = datetime.fromtimestamp(period_end, tz=UTC)

        session.commit()
        log.info("Subscription created for customer {}", customer_id)


def _handle_subscription_updated(data: dict, event_id: str, event_type: str) -> None:
    customer_id = data.get("customer")
    if not customer_id:
        return

    with use_db_session() as session:
        if not _mark_event_processed(session, event_id, event_type):
            log.debug("Duplicate event {} for customer {}", event_id, customer_id)
            return

        sub = _find_subscription_by_customer(session, customer_id)
        if not sub:
            log.error(
                "Received subscription.updated for unknown customer {}; will retry",
                customer_id,
            )
            raise _CustomerNotFoundError(customer_id)

        local_status, is_active = _map_stripe_status(data)

        # Preserve local CANCELING state: Stripe keeps status "active"
        # when cancel_at_period_end=True, but we track it separately.
        if local_status == SubscriptionStatus.ACTIVE.value and data.get(
            "cancel_at_period_end"
        ):
            local_status = SubscriptionStatus.CANCELING.value

        sub.subscription_status = local_status
        sub.is_active = is_active
        # Only promote to PLUS when subscription is actually active;
        # downgrade to FREE on cancellation/expiry so quota matches.
        # For multi-tier, resolve from data["items"]["data"][0]["price"]["id"].
        if is_active:
            sub.subscription_tier = SubscriptionTier.PLUS.value
        elif local_status == SubscriptionStatus.CANCELED.value:
            sub.subscription_tier = SubscriptionTier.FREE.value

        current_period = data.get("current_period_start")
        if current_period:
            sub.current_period_start = datetime.fromtimestamp(current_period, tz=UTC)
        period_end = data.get("current_period_end")
        if period_end:
            sub.current_period_end = datetime.fromtimestamp(period_end, tz=UTC)

        session.commit()
        log.info("Subscription updated for customer {}", customer_id)


def _handle_subscription_deleted(data: dict, event_id: str, event_type: str) -> None:
    customer_id = data.get("customer")
    if not customer_id:
        return

    with use_db_session() as session:
        if not _mark_event_processed(session, event_id, event_type):
            log.debug("Duplicate event {} for customer {}", event_id, customer_id)
            return

        sub = _find_subscription_by_customer(session, customer_id)
        if sub:
            sub.subscription_tier = SubscriptionTier.FREE.value
            sub.subscription_status = SubscriptionStatus.CANCELED.value
            sub.stripe_subscription_id = None
            sub.is_active = False
            # Reset usage so the user is not immediately quota-blocked
            # on the lower free tier daily limit.
            sub.current_period_usage = 0
            sub.daily_quota_reset_at = datetime.now(UTC)
            session.commit()
            log.info("Subscription canceled for customer {}", customer_id)
        else:
            log.warning(
                "Received {} for unknown customer {}; event marked processed",
                event_type,
                customer_id,
            )
            session.commit()


def _handle_payment_failed(data: dict, event_id: str, event_type: str) -> None:
    customer_id = data.get("customer")
    if not customer_id:
        return

    # Resolve the payment-error message BEFORE opening a DB connection
    # so the session is not held open during a Stripe network call.
    pi = data.get("payment_intent")
    error_msg = None
    if isinstance(pi, dict):
        raw_err = pi.get("last_payment_error")
        error_msg = raw_err.get("message") if isinstance(raw_err, dict) else None
    elif isinstance(pi, str) and ensure_stripe():
        try:
            import stripe

            pi_obj = stripe.PaymentIntent.retrieve(pi)
            raw_err = getattr(pi_obj, "last_payment_error", None)
            if raw_err:
                error_msg = getattr(raw_err, "message", None)
        except Exception:
            log.debug("Failed to retrieve PaymentIntent {} for error details", pi)

    with use_db_session() as session:
        if not _mark_event_processed(session, event_id, event_type):
            log.debug("Duplicate event {} for customer {}", event_id, customer_id)
            return

        result = session.execute(
            update(UserSubscription)
            .where(UserSubscription.stripe_customer_id == customer_id)
            .values(
                payment_status=PaymentStatus.FAILED.value,
                payment_failure_count=UserSubscription.payment_failure_count + 1,
                last_payment_error=error_msg,
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()
        if result.rowcount:
            log.warning("Payment failed for customer {}", customer_id)


def _handle_payment_succeeded(data: dict, event_id: str, event_type: str) -> None:
    customer_id = data.get("customer")
    if not customer_id:
        return

    with use_db_session() as session:
        if not _mark_event_processed(session, event_id, event_type):
            log.debug("Duplicate event {} for customer {}", event_id, customer_id)
            return

        sub = _find_subscription_by_customer(session, customer_id)
        if sub:
            sub.payment_status = PaymentStatus.CURRENT.value
            sub.payment_failure_count = 0
            sub.last_payment_error = None
            # Reset usage and advance period boundaries for the new billing cycle.
            # Also reset daily_quota_reset_at so ensure_daily_limit re-triggers
            # the day-boundary reset on the next request (prevents quota bypass).
            sub.current_period_usage = 0
            sub.daily_quota_reset_at = datetime(1970, 1, 1, tzinfo=UTC)
            period_start = data.get("period_start")
            if period_start:
                sub.current_period_start = datetime.fromtimestamp(period_start, tz=UTC)
            period_end = data.get("period_end")
            if period_end:
                sub.current_period_end = datetime.fromtimestamp(period_end, tz=UTC)
            session.commit()
            log.info("Payment succeeded for customer {}", customer_id)
        else:
            log.warning(
                "Received {} for unknown customer {}; event marked processed",
                event_type,
                customer_id,
            )
            session.commit()
