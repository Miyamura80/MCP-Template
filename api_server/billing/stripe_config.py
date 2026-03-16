"""Lazy Stripe initialization with graceful degradation."""

import threading

from loguru import logger as log

_stripe_initialized = False
_stripe_lock = threading.Lock()


def ensure_stripe() -> bool:
    """Initialize Stripe SDK. Returns False if no keys are configured.

    Negative results are never cached so that secrets injected after
    startup (e.g. Railway / Kubernetes delayed secret binding) are
    picked up on the next call without a restart.
    """
    global _stripe_initialized  # noqa: PLW0603
    if _stripe_initialized:
        return True

    with _stripe_lock:
        if _stripe_initialized:
            return True

        try:
            import stripe

            from common import global_config

            cfg = global_config.subscription_config.stripe

            # Pick the right key based on environment
            if global_config.DEV_ENV == "prod":
                key = global_config.STRIPE_SECRET_KEY
            else:
                key = global_config.STRIPE_TEST_SECRET_KEY
                if not key and global_config.STRIPE_SECRET_KEY:
                    log.warning(
                        "STRIPE_TEST_SECRET_KEY not set in non-prod environment; "
                        "falling back to live STRIPE_SECRET_KEY - real charges may occur"
                    )
                    key = global_config.STRIPE_SECRET_KEY

            if not key:
                log.debug("Stripe not configured - billing features disabled")
                return False

            stripe.api_key = key
            stripe.api_version = cfg.api_version
            _stripe_initialized = True
            log.info("Stripe SDK initialized (api_version={})", cfg.api_version)
            return True
        except Exception:
            log.warning("Failed to initialize Stripe; will retry on next call")
            return False


def get_stripe_price_id() -> str:
    """Return the Stripe price ID for the current environment."""
    from common import global_config

    cfg = global_config.subscription_config.stripe
    if global_config.DEV_ENV == "prod":
        return cfg.price_ids.get("prod", "")
    return cfg.price_ids.get("test", "")


def get_meter_event_name() -> str:
    """Return the Stripe Billing Meter event name."""
    from common import global_config

    return global_config.subscription_config.stripe.meter_event_name


def get_included_units() -> int:
    """Return the number of included metered units per period."""
    from common import global_config

    return global_config.subscription_config.metered.included_units


def get_webhook_secret() -> str | None:
    """Return the Stripe webhook signing secret for the current environment."""
    from common import global_config

    if global_config.DEV_ENV == "prod":
        return global_config.STRIPE_WEBHOOK_SECRET
    return (
        global_config.STRIPE_TEST_WEBHOOK_SECRET or global_config.STRIPE_WEBHOOK_SECRET
    )
