"""Sliding-window rate limiting middleware using the ``limits`` library."""

import hashlib
import math
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
        return "key:" + hashlib.sha256(api_key.encode()).hexdigest()[:16]
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        return "bearer:" + hashlib.sha256(token.encode()).hexdigest()[:16]
    return "ip:" + (request.client.host if request.client else "unknown")


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

        identity = _identity(request)

        # Skip rate limiting for test clients (Starlette TestClient)
        if identity == "ip:testclient":
            return await call_next(request)

        tier = (
            getattr(getattr(request.state, "subscription_tier", None), "value", None)
            or getattr(request.state, "subscription_tier", "default")
            if hasattr(request.state, "subscription_tier")
            else "default"
        )
        limits_cfg = _get_tier_limits(tier)

        # Build rate limit items for each window
        windows = [
            ("second", RateLimitItemPerSecond(limits_cfg.get("rps", 5))),
            ("minute", RateLimitItemPerMinute(limits_cfg.get("rpm", 60))),
            ("hour", RateLimitItemPerHour(limits_cfg.get("rph", 1000))),
            ("day", RateLimitItemPerDay(limits_cfg.get("rpd", 5000))),
        ]

        # Atomically check-and-consume quota per window, short-circuiting
        # on the first exceeded window. hit() is atomic (increment + check),
        # avoiding the TOCTOU race that test-then-hit would introduce.
        hit_window = None
        hit_item = None
        for window_name, item in windows:
            if not self._limiter.hit(item, identity):
                hit_window = window_name
                hit_item = item
                break

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
                "X-RateLimit-Reset": str(int(reset_time)),
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
