"""Billing page on the dashboard, deep-linkable so an agent at a limit can hand you one.

    /dashboard/billing?plan=plus        plan, today's usage, the plans; Upgrade
    POST /dashboard/billing/checkout    -> Stripe Checkout for Plus
    POST /dashboard/billing/portal      -> Stripe customer portal (card, invoices,
                                           cancel)

An agent that runs out of daily quota can't pay for you, so the quota error
carries this page's link (``billing_url``, built by ``dashboard/links.py``)
for the agent to pass on. Opening it never charges or changes anything: GET
reads only the database (not even Stripe) and highlights the plan the link
names. Both POSTs need the signed-in human's CSRF token, and the payment itself
is confirmed again on Stripe's own checkout page.

Query parameters (all optional; a value the page can't use is dropped, with a
note saying so):

- ``plan``: ``free`` or ``plus`` (``free_tier`` and ``plus_tier`` work too).
  Highlights that plan. A link names a plan and nothing else: price, trial and
  quantity come from server config, so no link can pick a price, a coupon or a
  seat count, and the button still has to be clicked.
- ``checkout``: ``success`` or ``cancel``, which Stripe's return URLs set.
  On ``success`` the Upgrade button gives way to "Confirming payment" until
  the webhook moves the plan to Plus, so a second click can't open a second
  subscription.

Everything is read for the signed-in user only, and the portal opens the
Stripe customer stored on that user's own subscription row. Checkout and the
portal are the helpers the REST billing routes use (``billing/checkout.py``).
The session holds no email: a first checkout, which has to create the Stripe
customer, looks it up from WorkOS by user id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse, Response

from api_server.auth.workos_auth import fetch_workos_email
from api_server.billing.checkout import (
    STRIPE_PAGE_HOSTS,
    EmailRequiredError,
    has_active_plus,
    open_billing_portal,
    start_checkout,
)
from api_server.dashboard.links import billing_url
from api_server.dashboard.session import (
    DashboardForm,
    DashboardSession,
    DashboardUser,
)
from api_server.dashboard.ui import box, csrf_field, dashboard_page, esc
from common import global_config
from db.engine import use_db_session
from db.models.subscription_types import SubscriptionStatus, SubscriptionTier
from db.models.user_subscriptions import UserSubscription

router = APIRouter(
    prefix="/dashboard/billing", tags=["dashboard"], include_in_schema=False
)

_FREE, _PLUS = SubscriptionTier.FREE.value, SubscriptionTier.PLUS.value
_PLANS = {"free": _FREE, "plus": _PLUS}
_NAMES = {_FREE: "Free", _PLUS: "Plus"}
_RETURNS = {
    "success": "Checkout finished. Your plan switches to Plus as soon as Stripe "
    "confirms the payment, usually within a few seconds.",
    "cancel": "Checkout cancelled. Nothing was charged.",
}
# Pill colour per subscription status; anything else (canceling) stays plain.
_TONES = {
    SubscriptionStatus.ACTIVE.value: "ok",
    SubscriptionStatus.TRIALING.value: "ok",
    SubscriptionStatus.PAST_DUE.value: "bad",
    SubscriptionStatus.INCOMPLETE.value: "bad",
    SubscriptionStatus.CANCELED.value: "bad",
}
# The page's forms end in a redirect to Stripe, which the browser checks
# against the CSP's form-action.
_FORM_TARGETS = tuple(f"https://{host}" for host in STRIPE_PAGE_HOSTS)
_NO_EMAIL = (
    "Couldn't start checkout: Stripe needs an email address for your billing "
    "account, and none is on file for this sign-in. Sign in with WorkOS (the "
    "development sign-in has no email) and try again."
)


@dataclass(frozen=True)
class _Account:
    """What the page shows; the defaults are a user with no subscription row."""

    tier: str = _FREE
    status: str = SubscriptionStatus.ACTIVE.value
    usage: int = 0
    plus: bool = False  # paying for (or trialing) Plus right now
    has_customer: bool = False
    period_end: datetime | None = None
    trial_end: datetime | None = None


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


def _account(user_id: str) -> _Account:
    """The user's plan and today's usage, from the database alone."""
    with use_db_session() as db:
        sub = db.query(UserSubscription).filter_by(user_id=user_id).first()
        if sub is None:
            return _Account()
        usage = sub.current_period_usage or 0
        reset = _aware(sub.daily_quota_reset_at)
        if reset is not None and reset.date() < datetime.now(UTC).date():
            usage = 0  # yesterday's count; ensure_daily_limit resets it lazily
        return _Account(
            tier=sub.subscription_tier,
            status=sub.subscription_status,
            usage=usage,
            plus=has_active_plus(sub),
            has_customer=bool(sub.stripe_customer_id),
            period_end=_aware(sub.current_period_end),
            trial_end=_aware(sub.trial_end),
        )


def _parse_plan(raw: str | None) -> tuple[str | None, str | None]:
    """``(tier or None, problem)`` for a ``plan`` value."""
    if not raw:
        return None, None
    tier = _PLANS.get(raw.strip().lower().removesuffix("_tier"))
    if tier is None:
        return None, f"Dropped plan {raw[:40]!r}: use free or plus."
    return tier, None


def _plan_note(account: _Account, plan: str) -> str:
    current = _PLUS if account.plus else _FREE
    if plan == current:
        return f"You're already on {_NAMES[plan]}."
    if plan == _PLUS:
        return (
            "Your link asks for Plus. Check it below, then click Upgrade: you "
            "confirm the payment on Stripe, nothing is charged before that."
        )
    return (
        "To move to Free, cancel Plus in the billing portal below. Plus stays on "
        "until the end of the period you paid for."
    )


def _daily_limit(tier: str) -> int | None:
    cfg = global_config.subscription_config.tier_limits.get(tier)
    return cfg.daily_requests if cfg else None


def _status_pill(account: _Account) -> str:
    tone = _TONES.get(account.status, "")
    label = esc(account.status.replace("_", " "))
    return f"<span class='pill {tone}'>{label}</span>"


def _current(account: _Account) -> str:
    limit = _daily_limit(account.tier)
    name = _NAMES.get(account.tier, account.tier)
    if limit:
        used = min(100, round(100 * account.usage / limit))
        meter = (
            f"<div class='meter' role='img' aria-label='{used}% of today&#39;s "
            f"requests used'><span style='width:{used}%'></span></div>"
            f"<p class='empty'><strong>{account.usage:,}</strong> of {limit:,} "
            "requests today. Resets at 00:00 UTC.</p>"
        )
    else:
        meter = "<p class='empty'>This plan has no daily requests configured.</p>"
    dates = []
    if account.status == SubscriptionStatus.TRIALING.value and account.trial_end:
        dates.append(f"trial ends {account.trial_end.date().isoformat()}")
    if account.plus and account.period_end:
        verb = (
            "ends" if account.status == SubscriptionStatus.CANCELING.value else "renews"
        )
        dates.append(f"{verb} {account.period_end.date().isoformat()}")
    pills = "".join(f"<span class='pill'>{esc(d)}</span>" for d in dates)
    return (
        f"<div class='meta'><span class='pill on'>{esc(name)}</span>"
        f"{_status_pill(account)}{pills}</div>{meter}"
    )


def _plans(
    session: DashboardSession, account: _Account, plan: str | None, confirming: bool
) -> str:
    trial = global_config.subscription_config.trial_period_days
    rows = []
    for tier, extra in (
        (_FREE, "No card needed"),
        (_PLUS, f"{trial}-day free trial" if trial else "Billed monthly"),
    ):
        current = (tier == _PLUS) == account.plus
        if current:
            action = "<span class='pill on'>Current</span>"
        elif tier == _PLUS and confirming:
            # Back from a paid checkout before Stripe's webhook landed: no
            # button, or a second click could open a second subscription.
            action = "<span class='pill'>Confirming payment</span>"
        elif tier == _PLUS:
            action = (
                "<form method='post' action='/dashboard/billing/checkout'>"
                + csrf_field(session)
                + "<button class='primary' type='submit'>Upgrade</button></form>"
            )
        else:
            action = "<span class='empty'>Cancel Plus below</span>"
        limit = _daily_limit(tier)
        rows.append(
            f"<tr{' class=pick' if tier == plan else ''}>"
            f"<td><strong>{_NAMES[tier]}</strong></td>"
            f"<td class='mono'>{f'{limit:,}' if limit is not None else 'n/a'}</td>"
            f"<td>{esc(extra)}</td><td>{action}</td></tr>"
        )
    return (
        "<table class=plans><thead><tr><th>Plan</th><th>Per day</th><th>Includes</th>"
        "<th></th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _page(
    session: DashboardSession,
    *,
    account: _Account | None = None,
    plan: str | None = None,
    confirming: bool = False,
    notes: list[str] | None = None,
    problem: str | None = None,
    status_code: int = 200,
) -> Response:
    account = account or _account(session.user_id)
    lead = "".join(f"<p class='note'>{esc(n)}</p>" for n in notes or [])
    if problem:
        lead = f"<p class='warn'>{esc(problem)}</p>" + lead
    manage = (
        box(
            "Manage",
            "<p class='empty'>Change your card, download invoices or cancel on "
            "Stripe's billing portal.</p>"
            "<form method='post' action='/dashboard/billing/portal'>"
            + csrf_field(session)
            + "<div class='actions'><button type='submit'>Open billing portal"
            "</button></div></form>",
        )
        if account.has_customer
        else ""
    )
    return dashboard_page(
        session,
        active="billing",
        eyebrow="Billing",
        title="Billing",
        lead="Your plan and today's usage. Paying happens on Stripe, after you click.",
        body=lead
        + box("Current plan", _current(account))
        + box("Plans", _plans(session, account, plan, confirming))
        + manage,
        status_code=status_code,
        form_action=_FORM_TARGETS,
    )


@router.get("")
def billing_page(
    session: DashboardUser,
    plan: Annotated[str | None, Query()] = None,
    checkout: Annotated[str | None, Query()] = None,
) -> Response:
    tier, plan_problem = _parse_plan(plan)
    notes = [plan_problem] if plan_problem else []
    if checkout in _RETURNS:
        notes.append(_RETURNS[checkout])
    elif checkout:
        notes.append(f"Dropped checkout {checkout[:40]!r}: use success or cancel.")
    account = _account(session.user_id)
    if tier is not None:
        notes.append(_plan_note(account, tier))
    return _page(
        session,
        account=account,
        plan=tier,
        confirming=checkout == "success",
        notes=notes,
    )


def _to_stripe(url: str | None) -> Response:
    """Redirect to a Stripe-hosted page, and nowhere else."""
    parts = urlsplit(url or "")
    if parts.scheme != "https" or parts.hostname not in STRIPE_PAGE_HOSTS:
        raise HTTPException(status_code=502, detail="Stripe returned no page to open")
    return RedirectResponse(parts.geturl(), status_code=303)


@router.post("/checkout")
def checkout_submit(session: DashboardForm) -> Response:
    try:
        with use_db_session() as db:
            url, _ = start_checkout(
                session.user_id,
                lambda: fetch_workos_email(session.user_id),
                db,
                success_url=billing_url("?checkout=success"),
                cancel_url=billing_url("?checkout=cancel"),
            )
        return _to_stripe(url)
    except EmailRequiredError as exc:
        return _page(
            session, plan=_PLUS, problem=_NO_EMAIL, status_code=exc.status_code
        )
    except HTTPException as exc:
        return _page(
            session,
            plan=_PLUS,
            problem=f"Couldn't start checkout: {exc.detail}",
            status_code=exc.status_code,
        )


@router.post("/portal")
def portal_submit(session: DashboardForm) -> Response:
    try:
        with use_db_session() as db:
            url = open_billing_portal(session.user_id, db, return_url=billing_url())
        return _to_stripe(url)
    except HTTPException as exc:
        return _page(
            session,
            problem=f"Couldn't open the billing portal: {exc.detail}",
            status_code=exc.status_code,
        )
