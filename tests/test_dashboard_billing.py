"""Dashboard billing page: deep-link prefill, CSRF, Stripe hand-off (mocked), IDOR."""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import stripe
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api_server.billing.limits import ensure_daily_limit
from api_server.dashboard import session as dash_session
from api_server.dashboard.billing import _parse_plan
from api_server.server import app
from common import global_config
from db.models.subscription_types import SubscriptionStatus, SubscriptionTier
from db.models.user_subscriptions import UserSubscription
from tests.test_mcp_e2e import _patch_db
from tests.test_template import TestTemplate

_PAGE = "/dashboard/billing"
_CHECKOUT_URL = "https://checkout.stripe.com/c/pay/cs_test_1"
_PORTAL_URL = "https://billing.stripe.com/p/session/test_1"
_CHECKOUT_MOD = "api_server.billing.checkout"


@contextmanager
def _client():
    """App client on an in-memory DB, dev sign-in, and every Stripe call mocked."""
    customer = MagicMock(return_value=SimpleNamespace(id="cus_new"))
    checkout = MagicMock(
        return_value=SimpleNamespace(url=_CHECKOUT_URL, id="cs_test_1")
    )
    portal = MagicMock(return_value=SimpleNamespace(url=_PORTAL_URL))
    stripe_subs: list[SimpleNamespace] = []
    subs = MagicMock(
        return_value=SimpleNamespace(auto_paging_iter=lambda: iter(stripe_subs))
    )
    email = MagicMock(return_value="human@example.com")
    with (
        _patch_db() as factory,
        patch.object(global_config, "WORKOS_CLIENT_ID", None),
        patch.object(global_config, "WORKOS_API_KEY", None),
        patch(f"{_CHECKOUT_MOD}.ensure_stripe", return_value=True),
        patch(f"{_CHECKOUT_MOD}.get_stripe_price_id", return_value="price_plus"),
        patch.object(stripe.Customer, "create", customer),
        patch.object(stripe.checkout.Session, "create", checkout),
        patch.object(stripe.billing_portal.Session, "create", portal),
        patch.object(stripe.Subscription, "list", subs),
        patch("api_server.dashboard.billing.fetch_workos_email", email),
        TestClient(app) as client,
    ):
        yield (
            client,
            factory,
            SimpleNamespace(
                customer=customer,
                checkout=checkout,
                portal=portal,
                subs=subs,
                stripe_subs=stripe_subs,
                email=email,
            ),
        )


def _login(client, user="human") -> str:
    """Sign in by minting the session cookie the WorkOS callback would set."""
    csrf = f"csrf-{user}"
    token = dash_session._serializer(dash_session._SESSION_SALT).dumps(
        {"uid": user, "csrf": csrf}
    )
    client.cookies.set(dash_session._cookie("session"), token)
    return csrf


def _add_sub(factory, user="human", **fields) -> None:
    with factory() as s:
        s.add(UserSubscription(user_id=user, **fields))
        s.commit()


def _sub(factory, user="human") -> UserSubscription | None:
    with factory() as s:
        return s.query(UserSubscription).filter_by(user_id=user).first()


def _plus(**extra) -> dict:
    return {
        "subscription_tier": SubscriptionTier.PLUS.value,
        "subscription_status": SubscriptionStatus.ACTIVE.value,
        "stripe_customer_id": "cus_human",
        **extra,
    }


class TestDeepLink(TestTemplate):
    def test_signed_out_link_survives_sign_in(self):
        with _client() as (client, _, _):
            first = client.get(f"{_PAGE}?plan=plus", follow_redirects=False)
            assert first.status_code == 303
            assert "/dashboard/login" in first.headers["location"]
            assert "plan%3Dplus" in first.headers["location"]

    def test_plus_link_prefills_and_changes_nothing(self):
        with _client() as (client, factory, mocks):
            _login(client)
            page = client.get(f"{_PAGE}?plan=plus")
            assert page.status_code == 200
            assert "Your link asks for Plus" in page.text
            assert re.search(r"<tr class=pick><td><strong>Plus", page.text)
            assert "aria-current=page>Billing</a>" in page.text
            assert _sub(factory) is None  # GET writes no subscription row
            mocks.customer.assert_not_called()
            mocks.checkout.assert_not_called()
            mocks.portal.assert_not_called()

    def test_csp_lets_the_forms_reach_stripe(self):
        with _client() as (client, _, _):
            _login(client)
            csp = client.get(_PAGE).headers["content-security-policy"]
            assert "form-action 'self' https://checkout.stripe.com" in csp
            assert "frame-ancestors 'none'" in csp

    @pytest.mark.parametrize(
        ("raw", "tier"),
        [
            ("plus", SubscriptionTier.PLUS.value),
            ("PLUS_tier", SubscriptionTier.PLUS.value),
            ("free", SubscriptionTier.FREE.value),
            (None, None),
        ],
    )
    def test_plan_values(self, raw, tier):
        assert _parse_plan(raw) == (tier, None)

    def test_bad_values_are_dropped_with_a_note(self):
        with _client() as (client, _, _):
            _login(client)
            page = client.get(f"{_PAGE}?plan=enterprise&checkout=maybe&price=price_x")
            assert page.status_code == 200
            assert "Dropped plan &#x27;enterprise&#x27;" in page.text
            assert "Dropped checkout" in page.text
            assert "class=pick" not in page.text
            assert "price_x" not in page.text  # no price ever comes from a link

    def test_dropped_value_is_escaped(self):
        with _client() as (client, _, _):
            _login(client)
            page = client.get(f"{_PAGE}?plan=<script>x</script>")
            assert "<script>x" not in page.text

    def test_return_notes(self):
        with _client() as (client, _, _):
            _login(client)
            assert "Checkout finished" in client.get(f"{_PAGE}?checkout=success").text
            assert "Nothing was charged" in client.get(f"{_PAGE}?checkout=cancel").text

    def test_back_from_checkout_shows_confirming_not_a_button(self):
        with _client() as (client, _, _):
            _login(client)
            page = client.get(f"{_PAGE}?checkout=success").text
            assert "Confirming payment" in page
            assert "action='/dashboard/billing/checkout'" not in page
            cancelled = client.get(f"{_PAGE}?checkout=cancel").text
            assert "action='/dashboard/billing/checkout'" in cancelled

    def test_already_on_the_linked_plan(self):
        with _client() as (client, factory, _):
            _add_sub(factory, **_plus())
            _login(client)
            page = client.get(f"{_PAGE}?plan=plus")
            assert "already on Plus" in page.text
            assert "action='/dashboard/billing/checkout'" not in page.text
            assert "Open billing portal" in page.text

    def test_free_link_while_on_plus_points_at_the_portal(self):
        with _client() as (client, factory, _):
            _add_sub(factory, **_plus())
            _login(client)
            assert (
                "cancel Plus in the billing portal"
                in client.get(f"{_PAGE}?plan=free").text
            )


class TestUsage(TestTemplate):
    def test_shows_todays_usage_against_the_limit(self):
        limit = global_config.subscription_config.tier_limits["free_tier"]
        with _client() as (client, factory, _):
            _add_sub(
                factory, current_period_usage=42, daily_quota_reset_at=datetime.now(UTC)
            )
            _login(client)
            page = client.get(_PAGE).text
            assert f"<strong>42</strong> of {limit.daily_requests:,}" in page
            assert "class='meter'" in page

    def test_yesterdays_count_reads_as_zero(self):
        with _client() as (client, factory, _):
            _add_sub(
                factory,
                current_period_usage=42,
                daily_quota_reset_at=datetime.now(UTC) - timedelta(days=1),
            )
            _login(client)
            assert "<strong>0</strong> of" in client.get(_PAGE).text

    def test_only_your_own_account_is_shown(self):
        with _client() as (client, factory, _):
            _add_sub(factory, user="eve", **_plus(stripe_customer_id="cus_eve"))
            _login(client, user="mallory")
            page = client.get(_PAGE).text
            assert "<span class='pill on'>Free</span>" in page
            assert "Open billing portal" not in page


class TestCheckout(TestTemplate):
    def test_needs_csrf(self):
        with _client() as (client, factory, mocks):
            _login(client)
            for path in ("/checkout", "/portal"):
                resp = client.post(f"{_PAGE}{path}", data={"csrf": "wrong"})
                assert resp.status_code == 403
            mocks.customer.assert_not_called()
            mocks.checkout.assert_not_called()
            mocks.portal.assert_not_called()
            assert _sub(factory) is None

    def test_get_on_the_post_routes_does_nothing(self):
        with _client() as (client, _, mocks):
            _login(client)
            # The /mcp mount at "/" answers paths FastAPI has no GET for.
            assert client.get(f"{_PAGE}/checkout").status_code in (404, 405)
            mocks.checkout.assert_not_called()

    def test_redirects_to_stripe_checkout(self):
        with _client() as (client, factory, mocks):
            csrf = _login(client)
            resp = client.post(
                f"{_PAGE}/checkout", data={"csrf": csrf}, follow_redirects=False
            )
            assert resp.status_code == 303
            assert resp.headers["location"] == _CHECKOUT_URL
            kwargs = mocks.checkout.call_args.kwargs
            assert kwargs["customer"] == "cus_new"
            assert kwargs["line_items"] == [{"price": "price_plus", "quantity": 1}]
            assert kwargs["success_url"].endswith("/dashboard/billing?checkout=success")
            assert kwargs["cancel_url"].endswith("/dashboard/billing?checkout=cancel")
            assert kwargs["idempotency_key"].startswith("checkout-human-price_plus-")
            mocks.email.assert_called_once_with("human")
            customer = mocks.customer.call_args.kwargs
            assert customer["email"] == "human@example.com"
            assert customer["idempotency_key"].startswith("create-customer-human-")
            mocks.subs.assert_not_called()  # a brand-new customer has no subs
            row = _sub(factory)
            assert row is not None and row.stripe_customer_id == "cus_new"

    def test_reuses_the_stored_customer(self):
        with _client() as (client, factory, mocks):
            _add_sub(factory, stripe_customer_id="cus_human")
            csrf = _login(client)
            client.post(
                f"{_PAGE}/checkout", data={"csrf": csrf}, follow_redirects=False
            )
            mocks.customer.assert_not_called()
            mocks.email.assert_not_called()  # only looked up for a new customer
            assert mocks.subs.call_args.kwargs["customer"] == "cus_human"
            assert mocks.checkout.call_args.kwargs["customer"] == "cus_human"

    @pytest.mark.parametrize("live", ["active", "trialing", "past_due"])
    def test_refused_when_stripe_has_a_sub_the_webhook_missed(self, live):
        with _client() as (client, factory, mocks):
            _add_sub(factory, stripe_customer_id="cus_human")  # row still says free
            mocks.stripe_subs.append(SimpleNamespace(status=live))
            csrf = _login(client)
            resp = client.post(f"{_PAGE}/checkout", data={"csrf": csrf})
            assert resp.status_code == 409
            mocks.checkout.assert_not_called()

    def test_canceled_stripe_subs_dont_block(self):
        with _client() as (client, factory, mocks):
            _add_sub(factory, stripe_customer_id="cus_human")
            mocks.stripe_subs.append(SimpleNamespace(status="canceled"))
            csrf = _login(client)
            resp = client.post(
                f"{_PAGE}/checkout", data={"csrf": csrf}, follow_redirects=False
            )
            assert resp.status_code == 303

    @pytest.mark.parametrize(
        "status", [SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIALING.value]
    )
    def test_refused_while_plus_is_live(self, status):
        with _client() as (client, factory, mocks):
            _add_sub(factory, **_plus(subscription_status=status))
            csrf = _login(client)
            resp = client.post(f"{_PAGE}/checkout", data={"csrf": csrf})
            assert resp.status_code == 409
            assert "already exists" in resp.text
            mocks.checkout.assert_not_called()

    def test_without_an_email_the_page_says_what_to_do(self):
        with _client() as (client, factory, mocks):
            mocks.email.return_value = None  # dev sign-in / WorkOS unset
            csrf = _login(client)
            resp = client.post(f"{_PAGE}/checkout", data={"csrf": csrf})
            assert resp.status_code == 422
            assert "Sign in with WorkOS" in resp.text
            mocks.customer.assert_not_called()
            assert _sub(factory) is None

    def test_billing_not_configured(self):
        with _client() as (client, _, mocks):
            csrf = _login(client)
            with patch(f"{_CHECKOUT_MOD}.ensure_stripe", return_value=False):
                resp = client.post(f"{_PAGE}/checkout", data={"csrf": csrf})
            assert resp.status_code == 503
            assert "Billing not configured" in resp.text
            mocks.customer.assert_not_called()

    def test_stripe_failure_is_a_page_not_a_500(self):
        with _client() as (client, _, mocks):
            mocks.checkout.configure_mock(side_effect=stripe.APIConnectionError("x"))
            csrf = _login(client)
            resp = client.post(f"{_PAGE}/checkout", data={"csrf": csrf})
            assert resp.status_code == 502
            assert "Stripe could not start checkout" in resp.text

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:x",
            "http://checkout.stripe.com/c/pay/x",
            "https://evil.example/c/pay/x",
            "https://checkout.stripe.com.evil.example/x",
            None,
        ],
    )
    def test_only_stripe_pages_are_followed(self, url):
        with _client() as (client, _, mocks):
            mocks.checkout.return_value = SimpleNamespace(url=url, id="c")
            csrf = _login(client)
            resp = client.post(
                f"{_PAGE}/checkout", data={"csrf": csrf}, follow_redirects=False
            )
            assert resp.status_code == 502
            assert "location" not in resp.headers


class TestPortal(TestTemplate):
    def test_opens_only_your_own_customer(self):
        with _client() as (client, factory, mocks):
            _add_sub(factory, user="eve", **_plus(stripe_customer_id="cus_eve"))
            _add_sub(factory, **_plus())
            csrf = _login(client)
            resp = client.post(
                f"{_PAGE}/portal", data={"csrf": csrf}, follow_redirects=False
            )
            assert resp.status_code == 303
            assert resp.headers["location"] == _PORTAL_URL
            kwargs = mocks.portal.call_args.kwargs
            assert kwargs["customer"] == "cus_human"
            assert kwargs["return_url"].endswith("/dashboard/billing")

    def test_no_billing_account_is_404(self):
        with _client() as (client, factory, mocks):
            _add_sub(factory, user="eve", **_plus(stripe_customer_id="cus_eve"))
            csrf = _login(client, user="mallory")
            resp = client.post(f"{_PAGE}/portal", data={"csrf": csrf})
            assert resp.status_code == 404
            mocks.portal.assert_not_called()


class TestQuotaErrorCarriesTheLink(TestTemplate):
    def _exhaust(self, factory, tier: str) -> dict:
        _add_sub(
            factory,
            user="agent-user",
            subscription_tier=tier,
            current_period_usage=10**9,
            daily_quota_reset_at=datetime.now(UTC),
        )
        with pytest.raises(HTTPException) as exc_info:
            ensure_daily_limit("agent-user")
        assert exc_info.value.status_code == 402
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        return detail

    def test_free_tier_gets_the_plus_deep_link(self):
        with _patch_db() as factory:
            detail = self._exhaust(factory, SubscriptionTier.FREE.value)
        url = detail["billing_url"]
        assert url.endswith("/dashboard/billing?plan=plus")
        # MCP clients only see the message, so the link must be in it too.
        assert detail["message"].endswith(f"upgrade at {url}")

    def test_plus_tier_gets_the_plain_page(self):
        with _patch_db() as factory:
            detail = self._exhaust(factory, SubscriptionTier.PLUS.value)
        assert detail["billing_url"].endswith("/dashboard/billing")
        assert "upgrade" not in detail["message"]
