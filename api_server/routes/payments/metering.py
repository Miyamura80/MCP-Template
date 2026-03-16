"""Metered usage reporting via Stripe Billing Meter API."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger as log
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api_server.auth import AuthenticatedUser
from api_server.auth.scopes import require_scopes
from api_server.billing.stripe_config import (
    ensure_stripe,
    get_included_units,
    get_meter_event_name,
)
from db.engine import get_db_session
from db.models.processed_stripe_events import ProcessedStripeEvent
from db.models.subscription_types import SubscriptionTier
from db.models.user_subscriptions import UserSubscription

router = APIRouter(prefix="/api/v1/billing/usage", tags=["billing"])


@router.post("/report")
def report_usage(
    request: Request,
    user: AuthenticatedUser = Depends(require_scopes("billing:write")),
    session: Session = Depends(get_db_session),
):
    """Report a single usage event via Stripe Billing Meter.

    This endpoint is for explicit metering (e.g. batch/external usage) and
    is separate from the daily quota enforced by ``ensure_daily_limit`` on
    service routes. Do not call this for actions that already pass through
    the services route, as it would double-count usage.

    Requires an ``Idempotency-Key`` header so that retries are safe: the
    key deduplicates both the Stripe MeterEvent and the local DB counter
    increment.
    """
    idempotency_key = request.headers.get("Idempotency-Key")
    if not idempotency_key:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key header is required for metering requests",
        )

    sub = session.query(UserSubscription).filter_by(user_id=user.user_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="No subscription found")
    if not sub.is_active:
        raise HTTPException(
            status_code=402, detail="No active subscription for metering"
        )

    from datetime import UTC, datetime

    # When an idempotency key is provided, check if we have already
    # processed this request.  Re-use the processed_stripe_events table
    # (PK-based dedup) so retries skip both the Stripe call and the local
    # counter increment, keeping the two in sync.
    if idempotency_key:
        try:
            session.add(
                ProcessedStripeEvent(
                    event_id=f"meter:{idempotency_key}",
                    event_type="metering.report_usage",
                )
            )
            session.flush()
        except IntegrityError:
            session.rollback()
            session.refresh(sub)
            log.debug("Duplicate metering request with key {}", idempotency_key)
            return {"usage": sub.current_period_usage}

    # Report to Stripe (identifier deduplicates on Stripe's side)
    stripe_ok = True
    if ensure_stripe() and sub.stripe_customer_id:
        import stripe

        identifier = idempotency_key or f"{sub.stripe_customer_id}-{uuid.uuid4().hex}"
        try:
            stripe.billing.MeterEvent.create(
                event_name=get_meter_event_name(),
                payload={
                    "stripe_customer_id": sub.stripe_customer_id,
                    "value": "1",
                },
                identifier=identifier,
            )
        except Exception:
            stripe_ok = False
            log.warning(
                "Failed to report meter event for customer {}; "
                "local counter will still increment (potential billing drift)",
                sub.stripe_customer_id,
            )

    # If Stripe failed, rollback the dedup record so the caller can retry
    # with the same idempotency key.  Do not increment the local counter.
    if not stripe_ok:
        session.rollback()
        session.refresh(sub)
        raise HTTPException(
            status_code=502,
            detail="Stripe meter event failed; retry with the same Idempotency-Key",
        )

    # Atomic increment to prevent lost updates under concurrent load
    session.execute(
        update(UserSubscription)
        .where(UserSubscription.user_id == user.user_id)
        .values(
            current_period_usage=UserSubscription.current_period_usage + 1,
            updated_at=datetime.now(UTC),
        )
    )
    session.commit()
    session.refresh(sub)

    return {"usage": sub.current_period_usage}


@router.get("/current")
def get_current_usage(
    user: AuthenticatedUser = Depends(require_scopes("billing:read")),
    session: Session = Depends(get_db_session),
):
    """Return current period usage, daily quota limit, and billing overage info."""
    from common import global_config

    sub_cfg = global_config.subscription_config
    sub = session.query(UserSubscription).filter_by(user_id=user.user_id).first()

    tier_key = sub.subscription_tier if sub else SubscriptionTier.FREE.value
    tier_cfg = sub_cfg.tier_limits.get(tier_key)
    daily_limit = tier_cfg.daily_requests if tier_cfg else 100

    if not sub:
        return {
            "usage": 0,
            "daily_limit": daily_limit,
            "billing_included_units": get_included_units(),
            "overage": 0,
        }

    included = get_included_units()
    overage = max(0, sub.current_period_usage - included)

    return {
        "usage": sub.current_period_usage,
        "daily_limit": daily_limit,
        "billing_included_units": included,
        "overage": overage,
        "period_start": sub.current_period_start.isoformat()
        if sub.current_period_start
        else None,
        "period_end": sub.current_period_end.isoformat()
        if sub.current_period_end
        else None,
    }
