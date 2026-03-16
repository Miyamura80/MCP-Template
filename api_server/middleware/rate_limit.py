"""Sliding-window rate limiting middleware using the ``limits`` library."""

import asyncio
import hashlib
import math
import os
import time

from fastapi import Request, Response
from limits import (
    RateLimitItemPerDay,
    RateLimitItemPerHour,
    RateLimitItemPerMinute,
    RateLimitItemPerSecond,
)
from limits.storage import MemoryStorage, Storage
from limits.strategies import MovingWindowRateLimiter
from loguru import logger as log
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

# Paths that bypass rate limiting
_EXEMPT_PATHS = frozenset({"/health", "/api/v1/billing/webhook/stripe"})

# TTL cache for API key hash → subscription tier (avoids DB hit on every request)
_tier_cache: dict[str, tuple[str, float]] = {}
_TIER_CACHE_TTL = 60  # seconds
_TIER_CACHE_MAX_SIZE = 10_000


def _build_storage() -> Storage:
    """Use Redis when REDIS_URL is set, otherwise in-memory."""
    try:
        from common import global_config

        redis_url = getattr(global_config, "REDIS_URL", None)
        if redis_url:
            from limits.storage import RedisStorage

            return RedisStorage(redis_url)
    except Exception:
        log.warning(
            "Redis unavailable for rate limiting, falling back to memory storage"
        )
    return MemoryStorage()


def _get_tier_limits(tier: str) -> dict:
    """Get rate limit values for a subscription tier from config."""
    try:
        from common import global_config

        rate_limit_cfg = getattr(global_config, "rate_limit", None)
        if rate_limit_cfg and hasattr(rate_limit_cfg, "tiers"):
            tiers = rate_limit_cfg.tiers
            if isinstance(tiers, dict):
                tier_cfg = tiers.get(tier, tiers.get("default", {}))
                if isinstance(tier_cfg, dict):
                    return tier_cfg
    except Exception:
        pass
    # Defaults if config not available
    return {"rps": 5, "rpm": 60, "rph": 1000, "rpd": 5000}


def _identity(request: Request) -> str:
    """Resolve a stable identity for rate limiting.

    Priority: API key hash > Bearer token prefix > client IP.
    """
    api_key = request.headers.get("X-API-KEY", "")
    if api_key:
        return "key:" + hashlib.sha256(api_key.encode()).hexdigest()
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        return "bearer:" + hashlib.sha256(token.encode()).hexdigest()
    # Use the last X-Forwarded-For entry (appended by the trusted edge proxy).
    # Earlier entries are client-supplied and spoofable.
    forwarded_ips = request.headers.get("X-Forwarded-For", "").split(",")
    forwarded = forwarded_ips[-1].strip() if forwarded_ips[-1].strip() else ""
    real_ip = (
        forwarded
        or request.headers.get("X-Real-IP", "")
        or (request.client.host if request.client else "unknown")
    )
    return "ip:" + real_ip


def _lookup_tier_sync(cache_key: str, *, user_id: str | None = None) -> str:
    """Look up subscription tier (synchronous, cached).

    Accepts either a cache_key derived from an API key hash or a user_id
    from a decoded JWT token. Uses a TTL cache to avoid a DB round-trip
    on every request.
    """
    cached = _tier_cache.get(cache_key)
    if cached and cached[1] > time.time():
        return cached[0]

    tier = "default"
    try:
        from db.engine import use_db_session
        from db.models.user_subscriptions import UserSubscription

        with use_db_session() as session:
            resolved_user_id = user_id
            if resolved_user_id is None:
                # cache_key is an API key hash - look up the user
                from db.models.api_keys import APIKey

                row = (
                    session.query(APIKey.user_id)
                    .filter_by(key_hash=cache_key, revoked=False)
                    .first()
                )
                resolved_user_id = row.user_id if row else None

            if resolved_user_id:
                sub = (
                    session.query(UserSubscription.subscription_tier)
                    .filter_by(user_id=resolved_user_id)
                    .first()
                )
                tier = sub.subscription_tier if sub else "default"
    except Exception:
        pass

    # Evict entries if cache is at capacity.
    # Iterate over snapshot lists to avoid RuntimeError from concurrent
    # dict mutation in asyncio.to_thread workers.
    if len(_tier_cache) >= _TIER_CACHE_MAX_SIZE:
        now = time.time()
        # First pass: remove expired entries
        expired = [k for k, (_, exp) in list(_tier_cache.items()) if exp <= now]
        for k in expired:
            _tier_cache.pop(k, None)
        # If still full, evict oldest 10% by expiry to avoid thundering herd
        if len(_tier_cache) >= _TIER_CACHE_MAX_SIZE:
            snapshot = list(_tier_cache.items())
            by_expiry = sorted(snapshot, key=lambda x: x[1][1])
            evict_count = max(1, len(by_expiry) // 10)
            for k, _ in by_expiry[:evict_count]:
                _tier_cache.pop(k, None)

    _tier_cache[cache_key] = (tier, time.time() + _TIER_CACHE_TTL)
    return tier


async def _resolve_tier(request: Request) -> str:
    """Resolve subscription tier from the request credentials.

    Performs a lightweight cached DB lookup so middleware doesn't depend on
    the auth dependency (which runs later, inside call_next).
    Supports both API key and JWT Bearer token authentication.
    """
    api_key = request.headers.get("X-API-KEY", "")
    if api_key:
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        return await asyncio.to_thread(_lookup_tier_sync, key_hash)

    # For JWT Bearer tokens, decode the user_id and look up tier
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            from api_server.auth.workos_auth import verify_workos_token

            token = auth_header.removeprefix("Bearer ").strip()
            workos_user = verify_workos_token(token)
            if workos_user:
                cache_key = f"jwt:{workos_user.user_id}"
                return await asyncio.to_thread(
                    _lookup_tier_sync, cache_key, user_id=workos_user.user_id
                )
        except Exception:
            pass

    return "default"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-request rate limiting with tier-aware sliding windows."""

    def __init__(self, app, **kwargs):
        super().__init__(app, **kwargs)
        self._storage = _build_storage()
        self._limiter = MovingWindowRateLimiter(self._storage)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Check if rate limiting is enabled
        try:
            from common import global_config

            rate_limit_cfg = getattr(global_config, "rate_limit", None)
            if (
                rate_limit_cfg
                and hasattr(rate_limit_cfg, "enabled")
                and not rate_limit_cfg.enabled
            ):
                return await call_next(request)
        except Exception:
            pass

        # Skip exempt paths
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        # Skip rate limiting in test mode
        if os.getenv("TESTING") == "1":
            return await call_next(request)

        identity = _identity(request)
        tier = await _resolve_tier(request)
        limits_cfg = _get_tier_limits(tier)

        # Build rate limit items for each window
        windows = [
            ("second", RateLimitItemPerSecond(limits_cfg.get("rps", 5))),
            ("minute", RateLimitItemPerMinute(limits_cfg.get("rpm", 60))),
            ("hour", RateLimitItemPerHour(limits_cfg.get("rph", 1000))),
            ("day", RateLimitItemPerDay(limits_cfg.get("rpd", 5000))),
        ]

        # Check all windows before consuming quota to avoid over-decrementing
        # earlier windows when a later window rejects. The narrow TOCTOU
        # between test() and hit() is acceptable: a concurrent request may
        # slip through, but guaranteed over-decrement under sustained load
        # is worse.
        hit_window = None
        hit_item = None
        for window_name, item in windows:
            if not self._limiter.test(item, identity):
                hit_window = window_name
                hit_item = item
                break

        if hit_window is None:
            # All windows have capacity - consume a slot in each
            for _, item in windows:
                self._limiter.hit(item, identity)

        # Use minute window for response headers
        _minute_item = windows[1][1]
        stats = self._limiter.get_window_stats(_minute_item, identity)
        remaining = max(0, stats.remaining)
        reset_time = stats.reset_time
        limit_val = limits_cfg.get("rpm", 60)

        if hit_window is not None and hit_item is not None:
            # Rate limited
            hit_stats = self._limiter.get_window_stats(hit_item, identity)
            retry_after = max(1, math.ceil(hit_stats.reset_time - time.time()))
            headers = {
                "X-RateLimit-Limit": str(limit_val),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(int(hit_stats.reset_time)),
                "RateLimit": f"limit={limit_val}, remaining=0",
                "RateLimit-Policy": f"{limits_cfg.get('rpm', 60)};w=60, {limits_cfg.get('rph', 1000)};w=3600",
                "Retry-After": str(retry_after),
            }
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": f"Rate limit exceeded ({hit_window} window). Retry after {retry_after}s.",
                        "request_id": getattr(request.state, "request_id", ""),
                    }
                },
                headers=headers,
            )

        response = await call_next(request)

        # Attach rate limit headers
        response.headers["X-RateLimit-Limit"] = str(limit_val)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(reset_time))
        response.headers["RateLimit"] = f"limit={limit_val}, remaining={remaining}"
        response.headers["RateLimit-Policy"] = (
            f"{limits_cfg.get('rpm', 60)};w=60, {limits_cfg.get('rph', 1000)};w=3600"
        )

        return response
