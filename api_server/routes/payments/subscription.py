"""Subscription status endpoint (dual-source: Stripe API + DB fallback)."""

import threading
import time

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

# TTL cache for Stripe subscription status to avoid a live API call on
# every request.  Keyed by stripe_subscription_id.
_stripe_status_cache: dict[str, tuple[str, float]] = {}
_stripe_status_lock = threading.Lock()
_STRIPE_STATUS_TTL = 60  # seconds
_STRIPE_ERROR_TTL = 5  # seconds -- short TTL to avoid thundering herd on outage
_STRIPE_ERROR_SENTINEL = "__error__"


def _get_stripe_status(stripe_sub_id: str) -> str | None:
    """Fetch Stripe subscription status with a 60s TTL cache."""
    cached = _stripe_status_cache.get(stripe_sub_id)
    if cached and cached[1] > time.time():
        return None if cached[0] == _STRIPE_ERROR_SENTINEL else cached[0]

    if not ensure_stripe():
        return None

    with _stripe_status_lock:
        # Double-check after acquiring lock
        cached = _stripe_status_cache.get(stripe_sub_id)
        if cached and cached[1] > time.time():
            return None if cached[0] == _STRIPE_ERROR_SENTINEL else cached[0]

    # Network I/O outside the lock so concurrent callers are not serialized
    # behind a single slow Stripe response.
    try:
        import stripe

        stripe_sub = stripe.Subscription.retrieve(stripe_sub_id)
        if stripe_sub.status == "active" and stripe_sub.cancel_at_period_end:
            status = "canceling"
        else:
            status = stripe_sub.status
        with _stripe_status_lock:
            _stripe_status_cache[stripe_sub_id] = (
                status,
                time.time() + _STRIPE_STATUS_TTL,
            )
        return status
    except Exception as exc:
        log.debug(
            "Stripe subscription lookup failed for {}: {}; falling back to DB",
            stripe_sub_id,
            exc,
        )
        with _stripe_status_lock:
            _stripe_status_cache[stripe_sub_id] = (
                _STRIPE_ERROR_SENTINEL,
                time.time() + _STRIPE_ERROR_TTL,
            )
        return None


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

    stripe_status = None
    if sub.stripe_subscription_id:
        stripe_status = _get_stripe_status(sub.stripe_subscription_id)

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
