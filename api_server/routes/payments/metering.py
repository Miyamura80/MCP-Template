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

    Pass an ``Idempotency-Key`` header to make retries safe: the key is
    used to deduplicate both the Stripe MeterEvent and the local DB
    counter increment.  Without a key, each call is treated as a unique
    event.
    """
    sub = session.query(UserSubscription).filter_by(user_id=user.user_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="No subscription found")

    from datetime import UTC, datetime

    idempotency_key = request.headers.get("Idempotency-Key")

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
            log.warning(
                "Failed to report meter event for customer {}",
                sub.stripe_customer_id,
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
    """Return current period usage and overage info."""
    sub = session.query(UserSubscription).filter_by(user_id=user.user_id).first()
    if not sub:
        return {"usage": 0, "included": get_included_units(), "overage": 0}

    included = get_included_units()
    overage = max(0, sub.current_period_usage - included)

    return {
        "usage": sub.current_period_usage,
        "included": included,
        "overage": overage,
        "period_start": sub.current_period_start.isoformat()
        if sub.current_period_start
        else None,
        "period_end": sub.current_period_end.isoformat()
        if sub.current_period_end
        else None,
    }
