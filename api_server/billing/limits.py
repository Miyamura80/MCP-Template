"""Daily usage limit enforcement."""

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models.subscription_types import SubscriptionTier
from db.models.user_subscriptions import UserSubscription


@dataclass
class LimitStatus:
    allowed: bool
    current_usage: int
    daily_limit: int
    remaining: int
    tier: str


def ensure_daily_limit(session: Session, user_id: str) -> LimitStatus:
    """Check and enforce the daily request limit for a user.

    Creates a free-tier subscription row if none exists.
    Raises HTTP 402 if the daily quota is exceeded.

    Uses an atomic UPDATE...WHERE to prevent race conditions under
    concurrent load.
    """
    from common import global_config

    cfg = global_config.subscription_config

    sub = session.query(UserSubscription).filter_by(user_id=user_id).first()
    if sub is None:
        try:
            sub = UserSubscription(
                user_id=user_id,
                subscription_tier=SubscriptionTier.FREE.value,
                current_period_start=datetime.now(UTC),
            )
            session.add(sub)
            session.commit()
            session.refresh(sub)
        except IntegrityError:
            session.rollback()
            sub = session.query(UserSubscription).filter_by(user_id=user_id).first()

    assert sub is not None  # guaranteed: created above or fetched after IntegrityError
    tier_key = sub.subscription_tier
    tier_cfg = cfg.tier_limits.get(tier_key)
    daily_limit = tier_cfg.daily_requests if tier_cfg else 100

    # Initialise current_period_start if missing (e.g., checkout-created rows)
    if sub.current_period_start is None:
        session.execute(
            update(UserSubscription)
            .where(
                UserSubscription.user_id == user_id,
                UserSubscription.current_period_start.is_(None),
            )
            .values(
                current_period_start=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()
        session.refresh(sub)

    # Atomic day-boundary reset: merge reset + first increment into one
    # statement so concurrent requests can't clobber each other's counts.
    if sub.current_period_start:
        now = datetime.now(UTC)
        if now.date() > sub.current_period_start.date():
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            result = session.execute(
                update(UserSubscription)
                .where(
                    UserSubscription.user_id == user_id,
                    UserSubscription.current_period_start < day_start,
                )
                .values(
                    current_period_usage=1,
                    current_period_start=now,
                    updated_at=now,
                )
            )
            session.commit()
            session.refresh(sub)
            if result.rowcount > 0:
                # We reset and claimed the first slot atomically
                return LimitStatus(
                    allowed=True,
                    current_usage=1,
                    daily_limit=daily_limit,
                    remaining=daily_limit - 1,
                    tier=tier_key,
                )
            # Another request already reset; fall through to normal increment

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
