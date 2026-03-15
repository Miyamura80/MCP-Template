"""Health-check endpoint with component status (no auth required)."""

import functools
import subprocess
from datetime import UTC, datetime

from fastapi import APIRouter

router = APIRouter(tags=["health"])


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
    """Check if Stripe is configured."""
    try:
        from common import global_config

        has_key = bool(
            getattr(global_config, "STRIPE_SECRET_KEY", None)
            or getattr(global_config, "STRIPE_TEST_SECRET_KEY", None)
        )
        return {"status": "ok" if has_key else "not_configured"}
    except Exception as exc:
        return {"status": "error", "message": type(exc).__name__}


@functools.cache
def _get_git_commit() -> str | None:
    """Get current git commit hash (cached at first call)."""
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


@router.get("/health")
def health_check():
    components = {
        "api": {"status": "ok"},
        "database": _check_database(),
        "redis": _check_redis(),
        "stripe": _check_stripe(),
    }

    # "ok" if all components are ok or not_configured; "degraded" if any errored
    overall = "ok"
    for comp in components.values():
        if comp["status"] == "error":
            overall = "degraded"
            break

    return {
        "status": overall,
        "version": "0.1.0",
        "commit": _get_git_commit(),
        "timestamp": datetime.now(UTC).isoformat(),
        "components": components,
    }
