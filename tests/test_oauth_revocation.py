"""Revoking every grant a user holds on the built-in OAuth server."""

from unittest.mock import ANY, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from api_server.dashboard import session as dashboard_session
from api_server.dashboard.ui import dashboard_page
from api_server.middleware.mcp_auth import _authenticate
from api_server.oauth.storage import revoke_user_grants
from db.models.oauth import OAuthCode, lock_user
from tests.oauth_server_fixture import (
    ISSUER,
    USER_ID,
    OAuthServerCase,
    bearer_scope,
    consent_form,
    pkce,
)
from tests.test_template import TestTemplate


class TestRevokeUserGrants(OAuthServerCase):
    def test_revokes_every_token_of_every_connected_app(self):
        tokens = []
        for _ in range(2):  # two connected apps
            code, verifier = self._sign_in()
            tokens.append(self._exchange(code, verifier))
        assert revoke_user_grants(USER_ID) == 4  # access + refresh, twice
        for pair in tokens:
            assert _authenticate(bearer_scope(pair["access_token"])) is None
            refreshed = self._token(
                grant_type="refresh_token", refresh_token=pair["refresh_token"]
            )
            assert refreshed["status"] == 400

    def test_spends_codes_not_yet_exchanged(self):
        code, verifier = self._sign_in()
        assert revoke_user_grants(USER_ID) == 0
        assert self._exchange(code, verifier)["status"] == 400
        with self.Session() as session:
            assert all(row.used_at for row in session.scalars(select(OAuthCode)))

    def test_refuses_a_sign_in_that_was_under_way(self):
        _, challenge = pkce()
        page = self.http.get(self._authorize(challenge))
        workos = self.http.post("/oauth/consent", data=consent_form(page.text, "allow"))
        state = parse_qs(urlsplit(workos.headers["location"]).query)["state"][0]
        revoke_user_grants(USER_ID)
        back = self.http.get(
            "/oauth/workos/callback", params={"code": "wc", "state": state}
        )
        assert back.status_code == 303
        query = parse_qs(urlsplit(back.headers["location"]).query)
        assert query["error"] == ["access_denied"]
        assert "code" not in query

    def test_signing_in_again_afterwards_works(self):
        revoke_user_grants(USER_ID)
        # Straight away, within the same second: the sign-in's start is kept
        # to the microsecond, so it is not mistaken for one begun earlier.
        code, verifier = self._sign_in()
        assert self._exchange(code, verifier)["status"] == 200

    def test_refresh_token_reuse_revokes_under_the_user_lock(self):
        code, verifier = self._sign_in()
        first = self._exchange(code, verifier)
        self._token(grant_type="refresh_token", refresh_token=first["refresh_token"])
        with patch("api_server.oauth.storage.lock_user") as locked:
            reuse = self._token(
                grant_type="refresh_token", refresh_token=first["refresh_token"]
            )
        assert reuse["status"] == 400
        locked.assert_any_call(ANY, USER_ID)


class TestSignOutEverywhere(OAuthServerCase):
    """The dashboard's "Sign out of all apps" is the entry point for sign-out."""

    def _dashboard(self) -> tuple[TestClient, str]:
        app = FastAPI()
        app.include_router(dashboard_session.router)
        dashboard_session.install(app)
        client = TestClient(app, base_url=ISSUER, follow_redirects=False)
        csrf = "csrf-token"
        cookie = dashboard_session._serializer(dashboard_session._SESSION_SALT).dumps(
            {"uid": USER_ID, "csrf": csrf}
        )
        client.cookies.set(dashboard_session._cookie("session"), cookie)
        return client, csrf

    def test_revokes_every_grant_and_ends_the_session(self):
        code, verifier = self._sign_in()
        pair = self._exchange(code, verifier)
        client, csrf = self._dashboard()
        response = client.post("/dashboard/sign-out-everywhere", data={"csrf": csrf})
        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard/login"
        assert "max-age=0" in response.headers["set-cookie"].lower()
        assert _authenticate(bearer_scope(pair["access_token"])) is None

    def test_requires_the_csrf_token(self):
        code, verifier = self._sign_in()
        pair = self._exchange(code, verifier)
        client, _ = self._dashboard()
        response = client.post("/dashboard/sign-out-everywhere", data={"csrf": "x"})
        assert response.status_code == 403
        assert _authenticate(bearer_scope(pair["access_token"])) is not None

    def test_dashboard_offers_it_in_self_mode(self):
        session = dashboard_session.DashboardSession(user_id=USER_ID, csrf="c")
        page = dashboard_page(session, active="", title="t", eyebrow="e", body="")
        assert "/dashboard/sign-out-everywhere" in bytes(page.body).decode()


class TestUserLock(TestTemplate):
    def test_postgres_takes_a_transaction_advisory_lock(self):
        session = MagicMock()
        session.get_bind.return_value.dialect.name = "postgresql"
        lock_user(session, USER_ID)
        statement = str(session.execute.call_args.args[0])
        assert "pg_advisory_xact_lock" in statement

    def test_other_databases_take_nothing(self):
        session = MagicMock()
        session.get_bind.return_value.dialect.name = "sqlite"
        lock_user(session, USER_ID)
        session.execute.assert_not_called()
