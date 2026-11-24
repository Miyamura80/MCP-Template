"""Stripe checkout session creation and subscription cancellation."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api_server.auth import AuthenticatedUser
from api_server.auth.scopes import BILLING_WRITE, require_scopes
from api_server.billing.checkout import start_checkout, stripe_errors
from api_server.billing.stripe_config import ensure_stripe
from common import global_config
from db.engine import get_db_session
from db.models.subscription_types import SubscriptionStatus
from db.models.user_subscriptions import UserSubscription

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


@router.post("/checkout/create")
def create_checkout(
    user: AuthenticatedUser = Depends(require_scopes(BILLING_WRITE)),
    session: Session = Depends(get_db_session),
):
    """Create a Stripe Checkout Session for the Plus tier."""
    frontend_url = global_config.FRONTEND_URL.rstrip("/")
    url, session_id = start_checkout(
        user.user_id,
        lambda: user.email,
        session,
        success_url=f"{frontend_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{frontend_url}/billing/cancel",
    )
    return {"checkout_url": url, "session_id": session_id}


@router.post("/cancel")
def cancel_subscription(
    user: AuthenticatedUser = Depends(require_scopes(BILLING_WRITE)),
    session: Session = Depends(get_db_session),
):
    """Cancel the user's Stripe subscription."""
    if not ensure_stripe():
        raise HTTPException(status_code=503, detail="Billing not configured")

    # Deliberately lazy: routes must import (and the app boot) without the
    # stripe SDK; ensure_stripe() above 503s instead.
    import stripe  # noqa: PLC0415

    sub = session.query(UserSubscription).filter_by(user_id=user.user_id).first()
    if not sub or not sub.stripe_subscription_id:
        raise HTTPException(status_code=404, detail="No active subscription found")

    if sub.subscription_status == SubscriptionStatus.CANCELING.value:
        return {"status": "cancel_scheduled"}

    if sub.subscription_status == SubscriptionStatus.CANCELED.value:
        raise HTTPException(status_code=409, detail="Subscription already canceled")

    with stripe_errors("cancel the subscription"):
        try:
            stripe.Subscription.modify(
                sub.stripe_subscription_id,
                cancel_at_period_end=True,
            )
        except stripe.InvalidRequestError as exc:
            # The caller's request is what Stripe rejected (e.g. an unknown
            # subscription), so it is a 400, not the 502 of other failures.
            raise HTTPException(
                status_code=400, detail=exc.user_message or str(exc)
            ) from exc

    # Mark as CANCELING - subscription stays active until period end.
    # The customer.subscription.deleted webhook sets CANCELED when it expires.
    sub.subscription_status = SubscriptionStatus.CANCELING.value
    session.commit()

    return {"status": "cancel_scheduled"}
