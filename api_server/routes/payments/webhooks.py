"""Stripe webhook handler with dual-secret verification."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from loguru import logger as log

from api_server.billing.stripe_config import ensure_stripe, get_webhook_secret
from db.engine import use_db_session
from db.models.subscription_types import (
    PaymentStatus,
    SubscriptionStatus,
    SubscriptionTier,
)
from db.models.user_subscriptions import UserSubscription

router = APIRouter(prefix="/api/v1/billing/webhook", tags=["billing"])


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

    if event_type == "customer.subscription.created":
        _handle_subscription_created(data, event_id)
    elif event_type == "customer.subscription.deleted":
        _handle_subscription_deleted(data, event_id)
    elif event_type == "invoice.payment_failed":
        _handle_payment_failed(data, event_id)
    elif event_type == "invoice.payment_succeeded":
        _handle_payment_succeeded(data, event_id)
    else:
        log.debug("Unhandled webhook event: {}", event_type)

    return {"received": True}


def _find_subscription_by_customer(
    session, customer_id: str
) -> UserSubscription | None:
    return (
        session.query(UserSubscription)
        .filter_by(stripe_customer_id=customer_id)
        .first()
    )


def _is_duplicate_event(sub: UserSubscription, event_id: str) -> bool:
    """Check if this Stripe event was already processed (at-least-once dedup)."""
    return sub.last_stripe_event_id == event_id


def _handle_subscription_created(data: dict, event_id: str) -> None:
    customer_id = data.get("customer")
    if not customer_id:
        return

    with use_db_session() as session:
        sub = _find_subscription_by_customer(session, customer_id)
        if not sub:
            log.error(
                "Received subscription.created for unknown customer {}; skipping",
                customer_id,
            )
            return

        if _is_duplicate_event(sub, event_id):
            log.debug("Duplicate event {} for customer {}", event_id, customer_id)
            return

        sub.stripe_subscription_id = data.get("id")
        sub.subscription_tier = SubscriptionTier.PLUS.value
        sub.subscription_status = SubscriptionStatus.ACTIVE.value
        sub.is_active = True
        sub.last_stripe_event_id = event_id

        current_period = data.get("current_period_start")
        if current_period:
            sub.current_period_start = datetime.fromtimestamp(current_period, tz=UTC)
        period_end = data.get("current_period_end")
        if period_end:
            sub.current_period_end = datetime.fromtimestamp(period_end, tz=UTC)

        session.commit()
        log.info("Subscription created for customer {}", customer_id)


def _handle_subscription_deleted(data: dict, event_id: str) -> None:
    customer_id = data.get("customer")
    if not customer_id:
        return

    with use_db_session() as session:
        sub = _find_subscription_by_customer(session, customer_id)
        if sub:
            if _is_duplicate_event(sub, event_id):
                log.debug("Duplicate event {} for customer {}", event_id, customer_id)
                return

            sub.subscription_tier = SubscriptionTier.FREE.value
            sub.subscription_status = SubscriptionStatus.CANCELED.value
            sub.stripe_subscription_id = None
            sub.last_stripe_event_id = event_id
            session.commit()
            log.info("Subscription canceled for customer {}", customer_id)


def _handle_payment_failed(data: dict, event_id: str) -> None:
    customer_id = data.get("customer")
    if not customer_id:
        return

    with use_db_session() as session:
        sub = _find_subscription_by_customer(session, customer_id)
        if sub:
            if _is_duplicate_event(sub, event_id):
                log.debug("Duplicate event {} for customer {}", event_id, customer_id)
                return

            sub.payment_status = PaymentStatus.FAILED.value
            sub.payment_failure_count += 1
            sub.last_payment_error = (
                data.get("last_payment_error", {}).get("message")
                if isinstance(data.get("last_payment_error"), dict)
                else str(data.get("last_payment_error", ""))
            )
            sub.last_stripe_event_id = event_id
            session.commit()
            log.warning(
                "Payment failed for customer {} (attempt {})",
                customer_id,
                sub.payment_failure_count,
            )


def _handle_payment_succeeded(data: dict, event_id: str) -> None:
    customer_id = data.get("customer")
    if not customer_id:
        return

    with use_db_session() as session:
        sub = _find_subscription_by_customer(session, customer_id)
        if sub:
            if _is_duplicate_event(sub, event_id):
                log.debug("Duplicate event {} for customer {}", event_id, customer_id)
                return

            sub.payment_status = PaymentStatus.CURRENT.value
            sub.payment_failure_count = 0
            sub.last_payment_error = None
            sub.last_stripe_event_id = event_id
            # Reset usage and advance period boundaries for the new billing cycle
            sub.current_period_usage = 0
            period_start = data.get("period_start")
            if period_start:
                sub.current_period_start = datetime.fromtimestamp(period_start, tz=UTC)
            period_end = data.get("period_end")
            if period_end:
                sub.current_period_end = datetime.fromtimestamp(period_end, tz=UTC)
            session.commit()
            log.info("Payment succeeded for customer {}", customer_id)
