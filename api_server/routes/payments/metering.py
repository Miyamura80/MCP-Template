"""Metered usage reporting via Stripe Billing Meter API."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger as log
from sqlalchemy import update
from sqlalchemy.orm import Session

from api_server.auth import AuthenticatedUser
from api_server.auth.scopes import require_scopes
from api_server.billing.stripe_config import (
    ensure_stripe,
    get_included_units,
    get_meter_event_name,
)
from db.engine import get_db_session
from db.models.user_subscriptions import UserSubscription

router = APIRouter(prefix="/api/v1/billing/usage", tags=["billing"])


@router.post("/report")
def report_usage(
    user: AuthenticatedUser = Depends(require_scopes("billing:write")),
    session: Session = Depends(get_db_session),
):
    """Report a single usage event via Stripe Billing Meter."""
    sub = session.query(UserSubscription).filter_by(user_id=user.user_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="No subscription found")

    from datetime import UTC, datetime

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

    # Report to Stripe if configured and user has a Stripe customer
    if ensure_stripe() and sub.stripe_customer_id:
        import stripe

        try:
            stripe.billing.MeterEvent.create(
                event_name=get_meter_event_name(),
                payload={
                    "stripe_customer_id": sub.stripe_customer_id,
                    "value": "1",
                },
                identifier=f"{sub.stripe_customer_id}-{uuid.uuid4().hex}",
            )
        except Exception:
            log.warning(
                "Failed to report meter event for customer {}",
                sub.stripe_customer_id,
            )

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
