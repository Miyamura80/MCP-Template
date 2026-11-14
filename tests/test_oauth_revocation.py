"""Revoking every grant a user holds on the built-in OAuth server."""

from unittest.mock import ANY, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select

from api_server.middleware.mcp_auth import _authenticate
from api_server.oauth.storage import revoke_user_grants
from db.models.oauth import OAuthCode, lock_user
from tests.oauth_server_fixture import (
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
        with patch("api_server.oauth.provider.lock_user") as locked:
            reuse = self._token(
                grant_type="refresh_token", refresh_token=first["refresh_token"]
            )
        assert reuse["status"] == 400
        locked.assert_any_call(ANY, USER_ID)


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
