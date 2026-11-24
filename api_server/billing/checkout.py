"""Stripe Checkout and customer-portal sessions for the Plus plan.

The one place that turns a user into a Stripe customer and a checkout session,
shared by the REST routes (``api_server/routes/payments/checkout.py``) and the
dashboard billing page (``api_server/dashboard/billing.py``). Every failure,
Stripe's included, is raised as an ``HTTPException`` for the caller to render.

Two guards keep a user to one subscription: the local row (updated by
webhooks) and, because a webhook can lag or fail, a live look at the Stripe
customer's subscriptions before each checkout. Two checkout sessions opened
side by side can still both be paid, so also turn on Stripe's "Limit customers
to one subscription" setting (Settings > Checkout).
"""

import hashlib
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from fastapi import HTTPException
from loguru import logger as log
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from api_server.billing.stripe_config import (
    ensure_stripe,
    get_stripe_price_id,
    reset_stripe_on_auth_error,
)
from common import global_config
from db.models.subscription_types import SubscriptionStatus, SubscriptionTier
from db.models.user_subscriptions import UserSubscription

# Where Stripe's hosted Checkout and customer portal live. A redirect to Stripe
# goes only here, and the pages that start one allow these as form targets.
STRIPE_PAGE_HOSTS = ("checkout.stripe.com", "billing.stripe.com")

_ACTIVE_PLUS_STATUSES = frozenset(
    {
        SubscriptionStatus.ACTIVE.value,
        # Every new subscriber starts here when trial_period_days is set; a
        # second checkout during the trial would create a second subscription.
        SubscriptionStatus.TRIALING.value,
        SubscriptionStatus.CANCELING.value,
        SubscriptionStatus.PAST_DUE.value,
        SubscriptionStatus.INCOMPLETE.value,
    }
)
# Stripe subscription statuses that still bill (or will) the customer.
_LIVE_STRIPE_STATUSES = frozenset({"active", "trialing", "past_due", "unpaid"})
_PLUS_EXISTS = "Active Plus subscription already exists"


class EmailRequiredError(HTTPException):
    """A first checkout needs an email for the new Stripe customer, and none is known."""

    def __init__(self) -> None:
        super().__init__(
            status_code=422,
            detail="An email address is required to create a billing account.",
        )


def has_active_plus(sub: UserSubscription | None) -> bool:
    """Whether *sub* already pays for (or is trialing) Plus, so checkout must not run."""
    return bool(
        sub
        and sub.subscription_tier == SubscriptionTier.PLUS.value
        and sub.subscription_status in _ACTIVE_PLUS_STATUSES
    )


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]


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
        # Deliberately lazy: routes must import (and the app boot) without
        # the stripe SDK; billing endpoints 503 via ensure_stripe() instead.
        import stripe  # noqa: PLC0415

        stripe.Customer.delete(orphaned_id)
    except Exception:  # noqa: BLE001
        # Best-effort cleanup: any Stripe API / network error here is non-fatal;
        # the orphan will eventually be cleaned by Stripe's GC or ops tooling.
        log.warning("Failed to delete orphaned Stripe customer {}", orphaned_id)


def _db_error(customer_id: str, user_id: str) -> HTTPException:
    log.error(
        "DB error persisting Stripe customer {} for user {} - customer may be "
        "orphaned in Stripe",
        customer_id,
        user_id,
    )
    return HTTPException(status_code=503, detail="Database error during checkout")


def _recover_concurrent_customer(
    session: Session, user_id: str, orphaned_customer_id: str
) -> tuple[str, UserSubscription | None]:
    """Handle IntegrityError from concurrent customer creation.

    Returns ``(customer_id, sub)`` after reconciling with the winner's row.
    Raises HTTP 409 if the winner already has an active Plus subscription.
    """
    sub = session.query(UserSubscription).filter_by(user_id=user_id).first()
    if sub and has_active_plus(sub):
        if sub.stripe_customer_id and orphaned_customer_id != sub.stripe_customer_id:
            _delete_orphaned_customer(
                orphaned_customer_id, user_id, sub.stripe_customer_id
            )
        raise HTTPException(status_code=409, detail=_PLUS_EXISTS) from None
    if sub and sub.stripe_customer_id:
        customer_id = sub.stripe_customer_id
        _delete_orphaned_customer(orphaned_customer_id, user_id, customer_id)
        return customer_id, sub
    if sub:
        sub.stripe_customer_id = orphaned_customer_id
        try:
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            raise _db_error(orphaned_customer_id, user_id) from None
    return orphaned_customer_id, sub


def _ensure_stripe_customer(
    user_id: str,
    resolve_email: Callable[[], str | None],
    session: Session,
    sub: UserSubscription | None,
) -> tuple[str, bool]:
    """Find or create the user's Stripe customer, persisting its ID.

    Returns ``(customer_id, created)``. ``resolve_email`` is called only when a
    customer has to be created, so callers can look the address up lazily.
    """
    # Deliberately lazy: stripe SDK optional (see _delete_orphaned_customer).
    import stripe  # noqa: PLC0415

    if sub and sub.stripe_customer_id:
        return sub.stripe_customer_id, False

    email = resolve_email()
    if not email:
        raise EmailRequiredError

    # Create the Stripe customer first (outside any DB lock) so a slow
    # Stripe round-trip doesn't hold a row lock and block other requests.
    # The idempotency key ensures concurrent calls return the same customer.
    customer_id = stripe.Customer.create(
        metadata={"user_id": user_id},
        email=email,
        idempotency_key=f"create-customer-{user_id}-{_digest(email)}",
    ).id

    # Lock the row briefly to serialize the DB write, re-checking in case
    # another request already stored a customer. A row deleted between the
    # two reads falls through to creating a fresh one.
    if sub:
        sub = (
            session.query(UserSubscription)
            .filter_by(user_id=user_id)
            .with_for_update()
            .first()
        )
    if sub and sub.stripe_customer_id:
        if customer_id != sub.stripe_customer_id:
            _delete_orphaned_customer(customer_id, user_id, sub.stripe_customer_id)
        return sub.stripe_customer_id, False
    if sub:
        sub.stripe_customer_id = customer_id
    else:
        session.add(UserSubscription(user_id=user_id, stripe_customer_id=customer_id))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        stored, _ = _recover_concurrent_customer(session, user_id, customer_id)
        return stored, stored == customer_id
    except SQLAlchemyError:
        session.rollback()
        raise _db_error(customer_id, user_id) from None
    return customer_id, True


@contextmanager
def stripe_errors(action: str) -> Iterator[None]:
    """Turn Stripe SDK failures into the HTTP errors the billing callers return."""
    # Deliberately lazy: stripe SDK optional (see _delete_orphaned_customer).
    import stripe  # noqa: PLC0415

    try:
        yield
    except stripe.AuthenticationError:
        reset_stripe_on_auth_error()
        raise HTTPException(
            status_code=503, detail="Stripe authentication failed; please retry"
        ) from None
    except stripe.StripeError:
        log.exception("Stripe failed to {}", action)
        raise HTTPException(
            status_code=502, detail=f"Stripe could not {action}; please retry"
        ) from None


def _has_live_subscription(customer_id: str) -> bool:
    """Ask Stripe itself, since the local row only changes when a webhook lands."""
    # Deliberately lazy: stripe SDK optional (see _delete_orphaned_customer).
    import stripe  # noqa: PLC0415

    subs = stripe.Subscription.list(customer=customer_id, status="all", limit=100)
    return any(s.status in _LIVE_STRIPE_STATUSES for s in subs.auto_paging_iter())


def start_checkout(
    user_id: str,
    resolve_email: Callable[[], str | None],
    session: Session,
    *,
    success_url: str,
    cancel_url: str,
) -> tuple[str | None, str]:
    """Create a Stripe Checkout Session for Plus; return ``(url, session_id)``.

    The price, trial and quantity always come from server config. Raises 409
    when the user already has Plus, by the local row or by Stripe.
    """
    if not ensure_stripe():
        raise HTTPException(status_code=503, detail="Billing not configured")

    # Deliberately lazy: stripe SDK optional (see _delete_orphaned_customer).
    import stripe  # noqa: PLC0415

    sub = session.query(UserSubscription).filter_by(user_id=user_id).first()
    if has_active_plus(sub):
        raise HTTPException(status_code=409, detail=_PLUS_EXISTS)

    price_id = get_stripe_price_id()
    if not price_id:
        raise HTTPException(status_code=503, detail="Stripe price ID not configured")

    with stripe_errors("start checkout"):
        customer_id, created = _ensure_stripe_customer(
            user_id, resolve_email, session, sub
        )
        if not created and _has_live_subscription(customer_id):
            raise HTTPException(status_code=409, detail=_PLUS_EXISTS)

        trial_days = global_config.subscription_config.trial_period_days
        # A 5-minute bucket dedupes double-clicks without blocking a retry of
        # an abandoned checkout for 24 hours; the URL digest keeps callers that
        # return to different pages from replaying each other's key.
        bucket = int(time.time()) // 300
        checkout_session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            subscription_data=({"trial_period_days": trial_days} if trial_days else {}),
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"user_id": user_id},
            idempotency_key=(
                f"checkout-{user_id}-{price_id}-{bucket}-"
                f"{_digest(success_url, cancel_url)}"
            ),
        )
    return checkout_session.url, checkout_session.id


def open_billing_portal(user_id: str, session: Session, *, return_url: str) -> str:
    """A Stripe customer-portal URL for *user_id*'s own Stripe customer.

    The portal is where a subscriber changes card, reads invoices or cancels.
    Raises 404 when the user has no Stripe customer yet (nothing to manage).
    """
    if not ensure_stripe():
        raise HTTPException(status_code=503, detail="Billing not configured")

    # Deliberately lazy: stripe SDK optional (see _delete_orphaned_customer).
    import stripe  # noqa: PLC0415

    sub = session.query(UserSubscription).filter_by(user_id=user_id).first()
    if not sub or not sub.stripe_customer_id:
        raise HTTPException(status_code=404, detail="No billing account yet")
    with stripe_errors("open the billing portal"):
        portal = stripe.billing_portal.Session.create(
            customer=sub.stripe_customer_id, return_url=return_url
        )
    return portal.url
