"""Sliding-window rate limiting middleware using the ``limits`` library."""

import asyncio
import hashlib
import math
import os
import threading
import time
import uuid

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

# Paths that bypass rate limiting.
from api_server.billing.stripe_config import STRIPE_WEBHOOK_PATH

_EXEMPT_PATHS = frozenset({"/health", STRIPE_WEBHOOK_PATH})

# TTL cache for API key hash → subscription tier (avoids DB hit on every request)
_tier_cache: dict[str, tuple[str, float]] = {}
_TIER_CACHE_TTL = 60  # seconds
_INVALID_KEY_TIER = "__invalid__"  # sentinel: API key not found in DB
_TIER_CACHE_MAX_SIZE = 10_000
_tier_cache_lock = threading.Lock()


def _build_storage() -> Storage:
    """Use Redis when REDIS_URL is set, otherwise in-memory."""
    redis_url = None
    try:
        from common import global_config

        redis_url = getattr(global_config, "REDIS_URL", None)
    except Exception:
        pass

    if redis_url:
        try:
            from limits.storage import RedisStorage

            return RedisStorage(redis_url)
        except Exception as exc:
            log.warning(
                "Redis unavailable for rate limiting ({}), falling back to memory storage",
                exc,
            )
            return MemoryStorage()

    log.debug(
        "REDIS_URL not set: rate limiting uses in-memory storage. "
        "Limits will not be enforced across multiple workers or replicas."
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
    except Exception as exc:
        log.warning("Rate limit config lookup failed; applying defaults: {}", exc)
    # Defaults if config not available
    return {"rps": 5, "rpm": 60, "rph": 1000, "rpd": 5000}


def _client_ip(request: Request) -> str:
    """Return the best-effort client IP from the request."""
    ip = request.headers.get("X-Real-IP", "").strip()
    if ip:
        return ip
    if request.client:
        return request.client.host
    return "unknown_" + uuid.uuid4().hex


async def _identity(request: Request) -> str:
    """Resolve a stable identity for rate limiting.

    Priority: API key hash > JWT user ID > client IP.
    JWT users are keyed on their stable user ID (not the ephemeral token
    hash) so that token rotation does not reset rate-limit counters.

    Only the blocking ``verify_workos_token`` call is offloaded via
    ``asyncio.to_thread``; all other paths are pure in-memory work.

    Caches the resolved ``_rl_user_id`` on ``request.state`` so that
    ``_resolve_tier`` can reuse it without calling ``verify_workos_token``
    a second time.
    """
    api_key = request.headers.get("X-API-KEY", "")
    if api_key:
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        # Validate the key before using it as a rate-limit bucket.
        # Without this, rotating fake keys creates unlimited fresh buckets
        # (same vulnerability the JWT path already guards against).
        tier = await asyncio.to_thread(_lookup_tier_sync, key_hash)
        if tier == _INVALID_KEY_TIER:
            return "ip:" + _client_ip(request)
        return "key:" + key_hash
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            from api_server.auth.workos_auth import verify_workos_token

            workos_user = await asyncio.to_thread(verify_workos_token, token)
            if workos_user:
                request.state._rl_user_id = workos_user.user_id
                return (
                    "user:" + hashlib.sha256(workos_user.user_id.encode()).hexdigest()
                )
        except Exception:
            pass
        # Mark that JWT resolution was attempted (even if it failed) so
        # _resolve_tier doesn't call verify_workos_token a second time.
        request.state._rl_user_id_resolved = True
        # Fall back to IP -- do NOT key on token hash, as rotating invalid
        # tokens would give each request a fresh bucket, bypassing rate limiting.
        return "ip:" + _client_ip(request)
    # Prefer X-Real-IP (set by nginx/Railway to the actual client IP).
    # DEPLOYMENT ASSUMPTION: This header is only trustworthy when the
    # server sits behind a reverse proxy (Railway, nginx, etc.) that
    # overwrites X-Real-IP with the true client address.  If the server
    # is directly reachable, clients can spoof this header to rotate
    # rate-limit buckets.  Do NOT expose the server without a proxy.
    # Skip X-Forwarded-For entirely for unauthenticated requests since
    # it is client-controlled and can be spoofed to rotate rate-limit
    # buckets.  Fall back to the TCP-level client address which cannot
    # be spoofed without controlling the connection.
    # Use a per-request UUID when no client info is available so that
    # truly-anonymous traffic doesn't share a single rate-limit bucket
    # (which would let one burst block all other unidentified clients).
    return "ip:" + _client_ip(request)


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
                # Reuse the canonical key validity check from api_key_auth
                from api_server.auth.api_key_auth import get_user_id_for_key_hash

                resolved_user_id = get_user_id_for_key_hash(session, cache_key)

            if resolved_user_id:
                sub = (
                    session.query(UserSubscription.subscription_tier)
                    .filter_by(user_id=resolved_user_id)
                    .first()
                )
                tier = sub.subscription_tier if sub else "default"
            elif user_id is None:
                # API key path: key hash not found in DB
                tier = _INVALID_KEY_TIER
    except Exception as exc:
        log.warning("Tier lookup failed for key {}; defaulting: {}", cache_key[:8], exc)
        # Don't cache failed lookups so the next request retries immediately
        return tier

    # Evict entries if cache is at capacity and then write the new entry,
    # all under the same lock to prevent the cache from transiently
    # exceeding _TIER_CACHE_MAX_SIZE under concurrent load.
    with _tier_cache_lock:
        if len(_tier_cache) >= _TIER_CACHE_MAX_SIZE:
            now = time.time()
            # First pass: remove expired entries
            expired = [k for k, (_, exp) in list(_tier_cache.items()) if exp <= now]
            for k in expired:
                _tier_cache.pop(k, None)
            # If still full, evict oldest 10% by expiry
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
        tier = await asyncio.to_thread(_lookup_tier_sync, key_hash)
        return "default" if tier == _INVALID_KEY_TIER else tier

    # For JWT Bearer tokens, reuse the user_id resolved by _identity()
    # (cached on request.state) to avoid calling verify_workos_token twice.
    cached_user_id = getattr(request.state, "_rl_user_id", None)
    if cached_user_id:
        cache_key = f"jwt:{cached_user_id}"
        return await asyncio.to_thread(
            _lookup_tier_sync, cache_key, user_id=cached_user_id
        )
    # If _identity already attempted JWT verification and it failed, don't
    # call verify_workos_token a second time; fall through to default.
    if getattr(request.state, "_rl_user_id_resolved", False):
        return "default"
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            from api_server.auth.workos_auth import verify_workos_token

            token = auth_header.removeprefix("Bearer ").strip()
            workos_user = await asyncio.to_thread(verify_workos_token, token)
            if workos_user:
                cache_key = f"jwt:{workos_user.user_id}"
                return await asyncio.to_thread(
                    _lookup_tier_sync, cache_key, user_id=workos_user.user_id
                )
        except Exception:
            pass

    return "default"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-request rate limiting with tier-aware sliding windows.

    Uses a two-phase ``test()`` then ``hit()`` approach instead of a
    single ``hit()`` per window.  This avoids over-decrementing earlier
    windows when a later window rejects, but introduces a TOCTOU gap
    where up to N concurrent workers can pass ``test()`` simultaneously
    before any of them calls ``hit()``.  Under sustained burst load this
    may briefly exceed the per-second limit.  The trade-off is acceptable
    for general API traffic; billing-critical endpoints are additionally
    protected by the atomic ``UPDATE...WHERE`` in ``ensure_daily_limit``.
    """

    _REDIS_RETRY_INTERVAL = 60  # seconds between Redis reconnect attempts

    def __init__(self, app, **kwargs):
        super().__init__(app, **kwargs)
        self._storage: Storage | None = None
        self._limiter: MovingWindowRateLimiter | None = None
        self._storage_is_memory = False
        self._last_storage_attempt = 0.0
        self._build_lock = threading.Lock()
        self._testing = os.getenv("TESTING") == "1"
        if self._testing:
            log.warning("Rate limiting disabled via TESTING=1 env var")

    def _get_limiter(self) -> MovingWindowRateLimiter:
        """Return the rate limiter, building storage lazily.

        If the previous attempt fell back to MemoryStorage (e.g. Redis
        was unreachable at startup), retry Redis periodically so that
        cross-worker enforcement recovers once Redis is healthy.
        """
        if self._limiter is not None and not self._storage_is_memory:
            return self._limiter
        # When using MemoryStorage, only retry Redis every _REDIS_RETRY_INTERVAL
        # seconds to avoid rebuilding storage (and losing counters) on every request.
        now = time.time()
        if (
            self._limiter is not None
            and self._storage_is_memory
            and now - self._last_storage_attempt < self._REDIS_RETRY_INTERVAL
        ):
            return self._limiter
        with self._build_lock:
            # Double-check after acquiring lock
            if self._limiter is not None and not self._storage_is_memory:
                return self._limiter
            now = time.time()
            if (
                self._limiter is not None
                and self._storage_is_memory
                and now - self._last_storage_attempt < self._REDIS_RETRY_INTERVAL
            ):
                return self._limiter
            self._last_storage_attempt = now
            storage = _build_storage()
            is_memory = isinstance(storage, MemoryStorage)
            # Only replace the limiter when Redis became available; keep the
            # existing MemoryStorage limiter so counters survive retries.
            if not is_memory or self._limiter is None:
                self._storage = storage
                self._limiter = MovingWindowRateLimiter(storage)
                self._storage_is_memory = is_memory
            return self._limiter

    def _check_and_hit(
        self, windows: list, identity: str
    ) -> tuple[str | None, object | None]:
        """Test all windows and consume a slot if allowed (synchronous).

        Offloaded to a thread via ``asyncio.to_thread`` so the blocking
        Redis I/O in ``test()`` / ``hit()`` does not stall the event loop.
        """
        for window_name, item in windows:
            if not self._get_limiter().test(item, identity):
                return window_name, item
        for _, item in windows:
            self._get_limiter().hit(item, identity)
        return None, None

    def _should_skip(self, request: Request) -> bool:
        """Return True if this request should bypass rate limiting."""
        if request.url.path in _EXEMPT_PATHS:
            return True
        if self._testing:
            return True
        try:
            from common import global_config

            rate_limit_cfg = getattr(global_config, "rate_limit", None)
            if (
                rate_limit_cfg
                and hasattr(rate_limit_cfg, "enabled")
                and not rate_limit_cfg.enabled
            ):
                return True
        except Exception:
            pass
        return False

    # Map window name → config key so 429 headers reflect the exceeded window.
    _WINDOW_CFG_KEY = {"second": "rps", "minute": "rpm", "hour": "rph", "day": "rpd"}

    def _build_429(
        self,
        request: Request,
        hit_window: str,
        hit_item,
        identity: str,
        limits_cfg: dict,
    ) -> JSONResponse:
        """Build a 429 Too Many Requests response."""
        cfg_key = self._WINDOW_CFG_KEY.get(hit_window, "rpm")
        exceeded_limit = limits_cfg.get(cfg_key, 60)
        try:
            hit_stats = self._get_limiter().get_window_stats(hit_item, identity)
            retry_after = max(1, math.ceil(hit_stats.reset_time - time.time()))
            reset_ts = int(hit_stats.reset_time)
        except Exception:
            retry_after = 60
            reset_ts = int(time.time()) + 60
        headers = {
            "X-RateLimit-Limit": str(exceeded_limit),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(reset_ts),
            "RateLimit": f"limit={exceeded_limit}, remaining=0, reset={reset_ts}",
            "RateLimit-Policy": f"{limits_cfg.get('rps', 5)};w=1, {limits_cfg.get('rpm', 60)};w=60, {limits_cfg.get('rph', 1000)};w=3600, {limits_cfg.get('rpd', 5000)};w=86400",
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

    def _most_constrained_stats(
        self, windows: list, identity: str, limits_cfg: dict
    ) -> tuple[int, int, float]:
        """Return (limit, remaining, reset_time) for the most constrained window."""
        remaining = None
        reset_time = time.time() + 60
        limit_val = limits_cfg.get("rpm", 60)
        for window_name, item in windows:
            try:
                s = self._get_limiter().get_window_stats(item, identity)
                if remaining is None or s.remaining < remaining:
                    remaining = s.remaining
                    cfg_key = self._WINDOW_CFG_KEY.get(window_name, "rpm")
                    limit_val = limits_cfg.get(cfg_key, 60)
                    reset_time = s.reset_time
            except Exception:
                pass
        return limit_val, max(0, remaining) if remaining is not None else 0, reset_time

    async def _resolve_request_context(
        self, request: Request
    ) -> tuple[str, str] | None:
        """Resolve identity and tier for a request.

        Returns ``(identity, tier)`` or ``None`` on failure.
        """
        try:
            identity = await _identity(request)
            tier = await _resolve_tier(request)
            return identity, tier
        except Exception:
            log.warning(
                "Rate limiter identity/tier resolution error; allowing request through"
            )
            return None

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if self._should_skip(request):
            return await call_next(request)

        ctx = await self._resolve_request_context(request)
        if ctx is None:
            return await call_next(request)
        identity, tier = ctx
        limits_cfg = _get_tier_limits(tier)

        # Build rate limit items for each window
        windows = [
            ("second", RateLimitItemPerSecond(limits_cfg.get("rps", 5))),
            ("minute", RateLimitItemPerMinute(limits_cfg.get("rpm", 60))),
            ("hour", RateLimitItemPerHour(limits_cfg.get("rph", 1000))),
            ("day", RateLimitItemPerDay(limits_cfg.get("rpd", 5000))),
        ]

        # Check all windows before consuming quota to avoid over-decrementing
        # earlier windows when a later window rejects.  Known trade-off: the
        # TOCTOU gap between test() and hit() means up to N concurrent
        # workers can pass test() simultaneously, briefly exceeding the RPS
        # limit.  This is acceptable because (a) over-decrementing under
        # sustained load is worse, and (b) the limits library's hit()
        # doesn't expose window stats needed for response headers.
        # Wrap in try/except so a Redis failure degrades gracefully (pass
        # through) rather than converting every request to a 500.
        try:
            hit_window, hit_item = await asyncio.to_thread(
                self._check_and_hit, windows, identity
            )
        except Exception:
            log.warning("Rate limiter error; allowing request through")
            return await call_next(request)

        if hit_window is not None and hit_item is not None:
            return self._build_429(request, hit_window, hit_item, identity, limits_cfg)

        # Report the most constrained window (lowest remaining) so clients
        # get an accurate backpressure signal before hitting harder limits.
        limit_val, remaining, reset_time = await asyncio.to_thread(
            self._most_constrained_stats, windows, identity, limits_cfg
        )

        response = await call_next(request)

        # Attach rate limit headers
        response.headers["X-RateLimit-Limit"] = str(limit_val)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(reset_time))
        response.headers["RateLimit"] = (
            f"limit={limit_val}, remaining={remaining}, reset={int(reset_time)}"
        )
        response.headers["RateLimit-Policy"] = (
            f"{limits_cfg.get('rps', 5)};w=1, {limits_cfg.get('rpm', 60)};w=60, {limits_cfg.get('rph', 1000)};w=3600, {limits_cfg.get('rpd', 5000)};w=86400"
        )

        return response
