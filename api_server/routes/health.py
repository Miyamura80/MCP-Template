"""Health-check endpoint with component status (no auth required)."""

import collections.abc
import functools
import os
import subprocess
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


def _cached_check(name: str, check_fn: collections.abc.Callable[[], dict]) -> dict:
    """Return cached result if fresh, otherwise call check_fn."""
    cached = _health_cache.get(name)
    now = time.monotonic()
    if cached and cached[1] > now:
        return cached[0]
    result = check_fn()
    _health_cache[name] = (result, now + _HEALTH_TTL)
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


def _check_redis() -> dict:
    """Check Redis connectivity."""
    try:
        from common import global_config

        redis_url = getattr(global_config, "REDIS_URL", None)
        if not redis_url:
            return {"status": "not_configured"}
        import redis

        r = redis.from_url(redis_url, socket_connect_timeout=2)
        try:
            r.ping()
        finally:
            r.close()
        return {"status": "ok"}
    except Exception as exc:
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


@functools.cache
def _get_git_commit() -> str | None:
    """Get current git commit hash (cached at first call).

    Prefers build-time env vars (GIT_SHA, RENDER_GIT_COMMIT) for
    containerized deployments where git may not be available.
    """
    for var in ("GIT_SHA", "RENDER_GIT_COMMIT"):
        val = os.getenv(var)
        if val:
            return val[:7]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


# Warm the cache at import time so the first health probe is fast.
_get_git_commit()


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
