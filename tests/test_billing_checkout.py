"""Shared Stripe checkout helpers via the REST routes, plus the WorkOS email lookup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
import stripe
from fastapi import HTTPException

from api_server.auth.unified_auth import AuthenticatedUser
from api_server.auth.workos_auth import fetch_workos_email
from api_server.branded_page import page_csp
from api_server.routes.payments.checkout import cancel_subscription, create_checkout
from common import global_config
from db.models.subscription_types import SubscriptionStatus
from tests.test_dashboard_billing import (
    _CHECKOUT_URL,
    _add_sub,
    _client,
    _login,
    _plus,
)
from tests.test_template import TestTemplate

_USER = AuthenticatedUser(user_id="api-user", email="a@example.com")


class TestRestCheckout(TestTemplate):
    def test_keeps_its_frontend_return_urls(self):
        with _client() as (_, factory, mocks), factory() as db:
            body = create_checkout(user=_USER, session=db)
        assert body == {"checkout_url": _CHECKOUT_URL, "session_id": "cs_test_1"}
        kwargs = mocks.checkout.call_args.kwargs
        frontend = global_config.FRONTEND_URL.rstrip("/")
        assert kwargs["cancel_url"] == f"{frontend}/billing/cancel"
        assert kwargs["idempotency_key"].startswith("checkout-api-user-price_plus-")

    def test_dashboard_and_rest_keys_differ_in_one_bucket(self):
        """Different return URLs must never share a Stripe idempotency key."""
        with _client() as (_, factory, mocks), factory() as db:
            create_checkout(user=_USER, session=db)
            rest_key = mocks.checkout.call_args.kwargs["idempotency_key"]
        with _client() as (client, factory, mocks):
            _add_sub(factory, user="api-user", stripe_customer_id="cus_x")
            csrf = _login(client, user="api-user")
            client.post(
                "/dashboard/billing/checkout",
                data={"csrf": csrf},
                follow_redirects=False,
            )
            dash_key = mocks.checkout.call_args.kwargs["idempotency_key"]
        assert rest_key.rsplit("-", 1)[0] == dash_key.rsplit("-", 1)[0]
        assert rest_key != dash_key

    def test_trialing_plus_is_409(self):
        trialing = _plus(
            stripe_customer_id="cus_api",
            subscription_status=SubscriptionStatus.TRIALING.value,
        )
        with _client() as (_, factory, mocks):
            _add_sub(factory, user="api-user", **trialing)
            with factory() as db, pytest.raises(HTTPException) as exc:
                create_checkout(user=_USER, session=db)
        assert exc.value.status_code == 409
        mocks.checkout.assert_not_called()


class TestRestCancel(TestTemplate):
    def _cancel(self, error: Exception) -> HTTPException:
        with (
            _client() as (_, factory, _),
            patch(
                "api_server.routes.payments.checkout.ensure_stripe", return_value=True
            ),
            patch.object(stripe.Subscription, "modify", MagicMock(side_effect=error)),
        ):
            _add_sub(factory, user="api-user", **_plus(stripe_subscription_id="sub_1"))
            with factory() as db, pytest.raises(HTTPException) as exc:
                cancel_subscription(user=_USER, session=db)
        return exc.value

    def test_invalid_request_stays_400(self):
        err = stripe.InvalidRequestError("No such subscription", param="id")
        assert self._cancel(err).status_code == 400

    def test_other_stripe_errors_are_502(self):
        exc = self._cancel(stripe.APIConnectionError("down"))
        assert exc.status_code == 502
        assert "cancel the subscription" in str(exc.detail)


class TestWorkosEmail(TestTemplate):
    def test_none_without_workos(self):
        with patch.object(global_config, "WORKOS_API_KEY", None):
            assert fetch_workos_email("user_1") is None

    def test_reads_the_user(self):
        response = httpx.Response(
            200,
            json={"id": "user_1", "email": "u@example.com"},
            request=httpx.Request("GET", "https://api.workos.com"),
        )
        with (
            patch.object(global_config, "WORKOS_API_KEY", "sk_test"),
            patch("httpx.get", return_value=response) as get,
        ):
            assert fetch_workos_email("user/1") == "u@example.com"
        assert get.call_args.args[0].endswith("/user_management/users/user%2F1")

    def test_failure_is_none(self):
        with (
            patch.object(global_config, "WORKOS_API_KEY", "sk_test"),
            patch("httpx.get", side_effect=httpx.ConnectError("down")),
        ):
            assert fetch_workos_email("user_1") is None


def test_page_csp_form_targets():
    assert "form-action 'self';" in page_csp()
    assert "form-action 'self' https://a.example;" in page_csp(("https://a.example",))
