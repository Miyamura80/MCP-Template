"""Stripe checkout session creation and subscription cancellation."""

import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api_server.auth import AuthenticatedUser, get_authenticated_user
from api_server.billing.stripe_config import ensure_stripe, get_stripe_price_id
from db.engine import get_db_session
from db.models.subscription_types import SubscriptionStatus, SubscriptionTier
from db.models.user_subscriptions import UserSubscription

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


@router.post("/checkout/create")
def create_checkout(
    user: AuthenticatedUser = Depends(get_authenticated_user),
    session: Session = Depends(get_db_session),
):
    """Create a Stripe Checkout Session for the Plus tier."""
    if not ensure_stripe():
        raise HTTPException(status_code=503, detail="Billing not configured")

    import stripe

    # Check for existing active subscription
    sub = session.query(UserSubscription).filter_by(user_id=user.user_id).first()
    if (
        sub
        and sub.subscription_tier == SubscriptionTier.PLUS.value
        and sub.subscription_status == SubscriptionStatus.ACTIVE.value
    ):
        raise HTTPException(
            status_code=409, detail="Active Plus subscription already exists"
        )

    # Find or create Stripe customer, persisting the ID to prevent duplicates
    customer_id = sub.stripe_customer_id if sub else None
    if not customer_id:
        customer = stripe.Customer.create(
            metadata={"user_id": user.user_id},
            email=user.email,
            idempotency_key=f"create-customer-{user.user_id}",
        )
        customer_id = customer.id
        if sub:
            sub.stripe_customer_id = customer_id
        else:
            sub = UserSubscription(
                user_id=user.user_id,
                stripe_customer_id=customer_id,
            )
            session.add(sub)
        session.commit()

    price_id = get_stripe_price_id()
    if not price_id:
        raise HTTPException(status_code=503, detail="Stripe price ID not configured")

    from common import global_config

    frontend_url = global_config.FRONTEND_URL.rstrip("/")

    checkout_session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=f"{frontend_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{frontend_url}/billing/cancel",
        metadata={"user_id": user.user_id},
        idempotency_key=f"checkout-{user.user_id}-{price_id}-{int(time.time())}",
    )

    return {"checkout_url": checkout_session.url, "session_id": checkout_session.id}


@router.post("/cancel")
def cancel_subscription(
    user: AuthenticatedUser = Depends(get_authenticated_user),
    session: Session = Depends(get_db_session),
):
    """Cancel the user's Stripe subscription."""
    if not ensure_stripe():
        raise HTTPException(status_code=503, detail="Billing not configured")

    import stripe

    sub = session.query(UserSubscription).filter_by(user_id=user.user_id).first()
    if not sub or not sub.stripe_subscription_id:
        raise HTTPException(status_code=404, detail="No active subscription found")

    stripe.Subscription.modify(
        sub.stripe_subscription_id,
        cancel_at_period_end=True,
    )

    # Mark as CANCELING - subscription stays active until period end.
    # The customer.subscription.deleted webhook sets CANCELED when it expires.
    sub.subscription_status = SubscriptionStatus.CANCELING.value
    session.commit()

    return {"status": "cancel_scheduled"}
