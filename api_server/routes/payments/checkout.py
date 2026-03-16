"""Stripe checkout session creation and subscription cancellation."""

import time

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger as log
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from api_server.auth import AuthenticatedUser
from api_server.auth.scopes import require_scopes
from api_server.billing.stripe_config import ensure_stripe, get_stripe_price_id
from db.engine import get_db_session
from db.models.subscription_types import SubscriptionStatus, SubscriptionTier
from db.models.user_subscriptions import UserSubscription

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


def _delete_orphaned_customer(orphaned_id: str, user_id: str, winner_id: str) -> None:
    """Best-effort cleanup of a Stripe customer orphaned by a checkout race."""
    log.warning(
        "Stripe customer {} orphaned due to concurrent checkout "
        "race for user {}; using existing customer {}",
        orphaned_id,
        user_id,
        winner_id,
    )
    try:
        import stripe

        stripe.Customer.delete(orphaned_id)
    except Exception:
        log.warning("Failed to delete orphaned Stripe customer {}", orphaned_id)


@router.post("/checkout/create")
def create_checkout(
    user: AuthenticatedUser = Depends(require_scopes("billing:write")),
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
        and sub.subscription_status
        in (SubscriptionStatus.ACTIVE.value, SubscriptionStatus.CANCELING.value)
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
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            orphaned_customer_id = customer_id
            sub = (
                session.query(UserSubscription).filter_by(user_id=user.user_id).first()
            )
            if (
                sub
                and sub.subscription_tier == SubscriptionTier.PLUS.value
                and sub.subscription_status
                in (
                    SubscriptionStatus.ACTIVE.value,
                    SubscriptionStatus.CANCELING.value,
                )
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Active Plus subscription already exists",
                ) from None
            # Only overwrite customer_id if the recovered row actually has one;
            # otherwise keep the Stripe customer we just created.
            if sub and sub.stripe_customer_id:
                customer_id = sub.stripe_customer_id
                _delete_orphaned_customer(
                    orphaned_customer_id, user.user_id, customer_id
                )
        except SQLAlchemyError:
            session.rollback()
            log.error(
                "DB error persisting Stripe customer {} for user {} - customer may be orphaned in Stripe",
                customer_id,
                user.user_id,
            )
            raise HTTPException(
                status_code=503,
                detail="Database error during checkout",
            ) from None

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
        # Include a 5-minute bucket so rapid double-clicks are deduped
        # but abandoned sessions don't block re-checkout for 24 hours.
        idempotency_key=f"checkout-{user.user_id}-{price_id}-{int(time.time()) // 300}",
    )

    return {"checkout_url": checkout_session.url, "session_id": checkout_session.id}


@router.post("/cancel")
def cancel_subscription(
    user: AuthenticatedUser = Depends(require_scopes("billing:write")),
    session: Session = Depends(get_db_session),
):
    """Cancel the user's Stripe subscription."""
    if not ensure_stripe():
        raise HTTPException(status_code=503, detail="Billing not configured")

    import stripe

    sub = session.query(UserSubscription).filter_by(user_id=user.user_id).first()
    if not sub or not sub.stripe_subscription_id:
        raise HTTPException(status_code=404, detail="No active subscription found")

    if sub.subscription_status == SubscriptionStatus.CANCELING.value:
        return {"status": "cancel_scheduled"}

    stripe.Subscription.modify(
        sub.stripe_subscription_id,
        cancel_at_period_end=True,
    )

    # Mark as CANCELING - subscription stays active until period end.
    # The customer.subscription.deleted webhook sets CANCELED when it expires.
    sub.subscription_status = SubscriptionStatus.CANCELING.value
    session.commit()

    return {"status": "cancel_scheduled"}
