"""Daily usage limit enforcement."""

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException
from loguru import logger as log
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.engine import use_db_session
from db.models.subscription_types import SubscriptionTier
from db.models.user_subscriptions import UserSubscription


@dataclass
class LimitStatus:
    allowed: bool
    current_usage: int
    daily_limit: int
    remaining: int
    tier: str


def _get_or_create_subscription(session: Session, user_id: str) -> UserSubscription:
    """Return the user's subscription, creating a free-tier row if needed."""
    sub = session.query(UserSubscription).filter_by(user_id=user_id).first()
    if sub is not None:
        return sub

    try:
        now = datetime.now(UTC)
        sub = UserSubscription(
            user_id=user_id,
            subscription_tier=SubscriptionTier.FREE.value,
            daily_quota_reset_at=now,
        )
        session.add(sub)
        session.commit()
        session.refresh(sub)
        return sub
    except IntegrityError:
        session.rollback()
        sub = session.query(UserSubscription).filter_by(user_id=user_id).first()

    if sub is None:
        raise RuntimeError(
            f"Failed to create or retrieve subscription for user {user_id}"
        )
    return sub


def ensure_daily_limit(user_id: str) -> LimitStatus:
    """Check and enforce the daily request limit for a user.

    Creates a free-tier subscription row if none exists.
    Raises HTTP 402 if the daily quota is exceeded.

    Uses its own DB session to avoid committing uncommitted work on
    a caller-provided session.  Uses an atomic UPDATE...WHERE to prevent
    race conditions under concurrent load.  Uses ``daily_quota_reset_at``
    (not ``current_period_start``) for day-boundary tracking so the
    Stripe billing period is never corrupted.
    """
    from common import global_config

    cfg = global_config.subscription_config

    with use_db_session() as session:
        sub = _get_or_create_subscription(session, user_id)

        tier_key = sub.subscription_tier
        tier_cfg = cfg.tier_limits.get(tier_key)
        if tier_cfg is None:
            log.error(
                "Unknown subscription tier {!r} for user {}; "
                "blocking request until tier is configured",
                tier_key,
                user_id,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "misconfiguration",
                    "message": "Subscription tier not configured",
                },
            )
        daily_limit = tier_cfg.daily_requests

        # Initialise daily_quota_reset_at if missing (e.g., pre-migration rows)
        reset_at = sub.daily_quota_reset_at
        if reset_at is None:
            now = datetime.now(UTC)
            session.execute(
                update(UserSubscription)
                .where(
                    UserSubscription.user_id == user_id,
                    UserSubscription.daily_quota_reset_at.is_(None),
                )
                .values(
                    daily_quota_reset_at=now,
                    updated_at=now,
                )
            )
            session.commit()
            # Refresh picks up the value we wrote, or the value another
            # request wrote if our WHERE-IS-NULL was a no-op.  Either way
            # reset_at ends up as a valid timestamp and we proceed normally.
            session.refresh(sub)
            reset_at = sub.daily_quota_reset_at

        # Atomic day-boundary reset: merge reset + first increment into one
        # statement so concurrent requests can't clobber each other's counts.
        # Only touches daily_quota_reset_at, never current_period_start.
        # reset_at is guaranteed non-None here (initialised above if missing).
        if reset_at is None:
            raise RuntimeError(
                f"daily_quota_reset_at unexpectedly None for user {user_id}"
            )
        now = datetime.now(UTC)
        if now.date() > reset_at.date():
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            # Zero-quota tiers: reset the clock without claiming a slot
            if daily_limit == 0:
                session.execute(
                    update(UserSubscription)
                    .where(
                        UserSubscription.user_id == user_id,
                        UserSubscription.daily_quota_reset_at < day_start,
                    )
                    .values(
                        current_period_usage=0,
                        daily_quota_reset_at=day_start,
                        updated_at=now,
                    )
                )
                session.commit()
                raise HTTPException(
                    status_code=402,
                    detail={
                        "code": "quota_exceeded",
                        "message": f"Daily request limit (0) exceeded for {tier_key} tier.",
                        "current_usage": 0,
                        "daily_limit": 0,
                        "tier": tier_key,
                    },
                )
            result = session.execute(
                update(UserSubscription)
                .where(
                    UserSubscription.user_id == user_id,
                    UserSubscription.daily_quota_reset_at < day_start,
                )
                .values(
                    current_period_usage=1,
                    daily_quota_reset_at=day_start,
                    updated_at=now,
                )
            )
            session.commit()
            session.refresh(sub)
            if result.rowcount > 0:
                return LimitStatus(
                    allowed=True,
                    current_usage=1,
                    daily_limit=daily_limit,
                    remaining=daily_limit - 1,
                    tier=tier_key,
                )
            # Another concurrent request already reset the counter.
            # Fall through to the normal atomic increment below.
            log.debug(
                "Day-reset race for user {}: another request won; falling through",
                user_id,
            )

        # Atomic increment: only succeeds if usage is still under the limit.
        result = session.execute(
            update(UserSubscription)
            .where(
                UserSubscription.user_id == user_id,
                UserSubscription.current_period_usage < daily_limit,
            )
            .values(
                current_period_usage=UserSubscription.current_period_usage + 1,
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()

        if result.rowcount == 0:
            session.refresh(sub)
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "quota_exceeded",
                    "message": f"Daily request limit ({daily_limit}) exceeded for {tier_key} tier.",
                    "current_usage": sub.current_period_usage,
                    "daily_limit": daily_limit,
                    "tier": tier_key,
                },
            )

        session.refresh(sub)
        new_remaining = max(0, daily_limit - sub.current_period_usage)
        return LimitStatus(
            allowed=True,
            current_usage=sub.current_period_usage,
            daily_limit=daily_limit,
            remaining=new_remaining,
            tier=tier_key,
        )
