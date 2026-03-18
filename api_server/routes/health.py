"""Health-check endpoint with component status (no auth required)."""

import collections.abc
import os
import subprocess
import threading
import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from fastapi import APIRouter

try:
    _APP_VERSION = _pkg_version("miyamura80-cli-template")
except PackageNotFoundError:
    _APP_VERSION = "0.1.0"

router = APIRouter(tags=["health"])

# TTL cache for component health checks (avoids DB/Redis hit on every poll)
_HEALTH_TTL = 15  # seconds
_health_cache: dict[str, tuple[dict, float]] = {}
_health_lock = threading.Lock()


def _cached_check(name: str, check_fn: collections.abc.Callable[[], dict]) -> dict:
    """Return cached result if fresh, otherwise call check_fn under lock.

    The check runs under the lock so only one thread probes at a time,
    preventing a thundering-herd of DB/Redis connections on cache miss.
    With a 15 s TTL the serialisation is negligible for health endpoints.
    """
    cached = _health_cache.get(name)
    now = time.monotonic()
    if cached and cached[1] > now:
        return cached[0]
    with _health_lock:
        # Double-check after acquiring lock
        cached = _health_cache.get(name)
        now = time.monotonic()
        if cached and cached[1] > now:
            return cached[0]
        result = check_fn()
        _health_cache[name] = (result, time.monotonic() + _HEALTH_TTL)
    return result


def _check_database() -> dict:
    """Check database connectivity using the app's singleton engine."""
    try:
        from common import global_config

        if not global_config.BACKEND_DB_URI:
            return {"status": "not_configured"}
        from sqlalchemy import text

        from db.engine import use_db_session

        with use_db_session() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "message": type(exc).__name__}


_redis_health_client: object | None = None
_redis_health_client_lock = threading.Lock()
_REDIS_NOT_CONFIGURED = object()  # sentinel: REDIS_URL was absent


def _get_redis_health_client():
    """Return a reusable Redis client for health checks.

    Returns ``None`` when no ``REDIS_URL`` is configured (the
    ``_REDIS_NOT_CONFIGURED`` sentinel is used internally to
    distinguish "not configured" from "not yet created").
    Creates and caches a new client on first call, then returns
    the cached instance on subsequent calls.
    """
    global _redis_health_client  # noqa: PLW0603
    val = _redis_health_client
    if val is _REDIS_NOT_CONFIGURED:
        return None
    if val is not None:
        return val
    with _redis_health_client_lock:
        val = _redis_health_client
        if val is _REDIS_NOT_CONFIGURED:
            return None
        if val is not None:
            return val
        from common import global_config

        redis_url = getattr(global_config, "REDIS_URL", None)
        if not redis_url:
            _redis_health_client = _REDIS_NOT_CONFIGURED
            return None
        import redis

        _redis_health_client = redis.from_url(
            redis_url, socket_connect_timeout=2, socket_timeout=2
        )
        return _redis_health_client


def _check_redis() -> dict:
    """Check Redis connectivity."""
    global _redis_health_client  # noqa: PLW0603
    try:
        client = _get_redis_health_client()
        if client is None:
            return {"status": "not_configured"}
        client.ping()
        return {"status": "ok"}
    except Exception as exc:
        # Reset so the next health probe re-creates the client
        _redis_health_client = None
        return {"status": "error", "message": type(exc).__name__}


def _check_stripe() -> dict:
    """Check Stripe SDK initialization (config + key presence).

    Does not make a live API call to avoid hitting Stripe rate limits
    from frequent health probes.
    """
    try:
        from common import global_config

        has_key = bool(
            getattr(global_config, "STRIPE_SECRET_KEY", None)
            or getattr(global_config, "STRIPE_TEST_SECRET_KEY", None)
        )
        if not has_key:
            return {"status": "not_configured"}

        from api_server.billing.stripe_config import ensure_stripe

        if not ensure_stripe():
            return {"status": "error", "message": "initialization_failed"}

        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "message": type(exc).__name__}


_git_commit_lock = threading.Lock()
_git_commit_value: str | None = None
_git_commit_resolved = False


def _get_git_commit() -> str | None:
    """Get current git commit hash (cached after first call).

    Prefers build-time env vars (GIT_SHA, RENDER_GIT_COMMIT) for
    containerized deployments where git may not be available.
    Production deployments should always set GIT_SHA so the subprocess
    fallback is never invoked (it adds latency on cold start and requires
    git to be installed in the container image).
    Uses double-checked locking so the subprocess runs exactly once,
    even under concurrent cold-start calls.  Executes in FastAPI's
    sync-endpoint threadpool, so it does not block the async event loop.
    """
    global _git_commit_value, _git_commit_resolved
    if _git_commit_resolved:
        return _git_commit_value
    with _git_commit_lock:
        if _git_commit_resolved:
            return _git_commit_value
        for var in ("GIT_SHA", "RENDER_GIT_COMMIT"):
            # Prefer build-time env vars; the subprocess fallback below
            # requires git in the container image and adds cold-start latency.
            val = os.getenv(var)
            if val:
                _git_commit_value = val[:7]
                _git_commit_resolved = True
                return _git_commit_value
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            _git_commit_value = (
                result.stdout.strip() if result.returncode == 0 else None
            )
        except Exception:
            _git_commit_value = None
        _git_commit_resolved = True
        return _git_commit_value


@router.get("/health")
def health_check():
    components = {
        "api": {"status": "ok"},
        "database": _cached_check("database", _check_database),
        "redis": _cached_check("redis", _check_redis),
        "stripe": _cached_check("stripe", _check_stripe),
    }

    # "ok" if all components are ok or not_configured; "degraded" if any errored
    overall = "ok"
    for comp in components.values():
        if comp["status"] == "error":
            overall = "degraded"
            break

    return {
        "status": overall,
        "version": _APP_VERSION,
        "commit": _get_git_commit(),
        "timestamp": datetime.now(UTC).isoformat(),
        "components": components,
    }
