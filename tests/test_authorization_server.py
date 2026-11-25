"""The MCP authorization-server strategy picked by ``MCP_AUTH_MODE``."""

from unittest.mock import patch

from fastapi import FastAPI

from api_server.auth.authorization_server import (
    AuthKitAuthorizationServer,
    BuiltinAuthorizationServer,
    authorization_server,
)
from api_server.dashboard.session import DashboardSession
from api_server.dashboard.ui import dashboard_page
from common import global_config
from tests.test_template import TestTemplate


class TestStrategySelection(TestTemplate):
    def test_default_is_authkit(self):
        assert isinstance(authorization_server(), AuthKitAuthorizationServer)

    def test_follows_a_patched_mode(self):
        with patch.object(global_config, "MCP_AUTH_MODE", "self"):
            assert isinstance(authorization_server(), BuiltinAuthorizationServer)
        assert isinstance(authorization_server(), AuthKitAuthorizationServer)

    def test_chosen_once_per_mode(self):
        assert authorization_server() is authorization_server()


class TestAuthKitStrategy(TestTemplate):
    def test_is_inert(self):
        server = AuthKitAuthorizationServer()
        app = FastAPI()
        before = list(app.router.routes)
        server.mount(app)
        assert app.router.routes == before
        assert server.issues_tokens is False
        assert server.authenticate_bearer("anything") is None
        assert server.revoke_user_grants("user_1") == 0

    def test_names_the_authkit_domain(self):
        with patch.object(
            global_config, "WORKOS_AUTHKIT_DOMAIN", "https://auth.example.com/"
        ):
            url = AuthKitAuthorizationServer().authorization_server_url()
        assert url == "https://auth.example.com"

    def test_unconfigured_names_no_issuer(self):
        with patch.object(global_config, "WORKOS_AUTHKIT_DOMAIN", None):
            assert AuthKitAuthorizationServer().authorization_server_url() is None


class TestDashboardSignOut(TestTemplate):
    def _page(self) -> str:
        session = DashboardSession(user_id="user_1", csrf="c")
        page = dashboard_page(session, active="", title="t", eyebrow="e", body="")
        return bytes(page.body).decode()

    def test_authkit_mode_signs_out_of_this_browser(self):
        page = self._page()
        assert "/dashboard/logout" in page
        assert "/dashboard/sign-out-everywhere" not in page

    def test_self_mode_signs_out_of_every_app(self):
        with patch.object(global_config, "MCP_AUTH_MODE", "self"):
            page = self._page()
        assert "/dashboard/sign-out-everywhere" in page
        assert "Sign out of all apps" in page
