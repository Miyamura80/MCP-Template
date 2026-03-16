"""Subscription status endpoint (dual-source: Stripe API + DB fallback)."""

from fastapi import APIRouter, Depends
from loguru import logger as log
from sqlalchemy.orm import Session

from api_server.auth import AuthenticatedUser
from api_server.auth.scopes import require_scopes
from api_server.billing.stripe_config import ensure_stripe
from db.engine import get_db_session
from db.models.subscription_types import SubscriptionTier
from db.models.user_subscriptions import UserSubscription

router = APIRouter(prefix="/api/v1/billing/subscription", tags=["billing"])


@router.get("/status")
def subscription_status(
    user: AuthenticatedUser = Depends(require_scopes("billing:read")),
    session: Session = Depends(get_db_session),
):
    """Return current subscription tier, status, usage, and payment info."""
    sub = session.query(UserSubscription).filter_by(user_id=user.user_id).first()

    if not sub:
        return {
            "tier": SubscriptionTier.FREE.value,
            "status": "active",
            "usage": 0,
            "payment_status": "current",
        }

    # Try Stripe for authoritative status if available
    stripe_status = None
    if ensure_stripe() and sub.stripe_subscription_id:
        try:
            import stripe

            stripe_sub = stripe.Subscription.retrieve(sub.stripe_subscription_id)
            # Preserve local CANCELING: Stripe returns "active" when
            # cancel_at_period_end=True, but we track cancellation separately.
            if stripe_sub.status == "active" and stripe_sub.cancel_at_period_end:
                stripe_status = "canceling"
            else:
                stripe_status = stripe_sub.status
        except Exception as exc:
            log.debug(
                "Stripe subscription lookup failed for {}: {}; falling back to DB",
                sub.stripe_subscription_id,
                exc,
            )

    return {
        "tier": sub.subscription_tier,
        "status": stripe_status or sub.subscription_status,
        "usage": sub.current_period_usage,
        "payment_status": sub.payment_status,
        "period_start": sub.current_period_start.isoformat()
        if sub.current_period_start
        else None,
        "period_end": sub.current_period_end.isoformat()
        if sub.current_period_end
        else None,
    }
