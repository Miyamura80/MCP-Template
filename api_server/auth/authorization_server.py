"""Which authorization server issues the tokens MCP clients send to /mcp.

``MCP_AUTH_MODE`` picks one of two strategies, and every place that behaves
differently per mode asks :func:`authorization_server` instead of reading the
setting itself:

    authkit (default)  WorkOS AuthKit issues MCP tokens; no OAuth endpoints
                       are mounted here.
    self               the built-in server in ``api_server.oauth`` issues,
                       stores and can revoke them; AuthKit only signs users in.

The ``self`` strategy imports its OAuth modules inside each method, so this
module never pulls in the OAuth storage or provider on its own.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING, Protocol

from api_server.auth.authkit_auth import authkit_domain, resolve_token_scopes
from api_server.auth.unified_auth import AuthenticatedUser
from common import global_config

if TYPE_CHECKING:
    from fastapi import FastAPI


class McpAuthorizationServer(Protocol):
    """The mode-specific behavior of the server's MCP authorization."""

    issues_tokens: bool
    """True when this server issues, stores and can revoke MCP tokens."""

    sign_out: tuple[str, str]
    """The dashboard's sign-out control: (form action, button label)."""

    def mount(self, app: FastAPI) -> None:
        """Add this server's OAuth endpoints, if it has any, to ``app``."""
        ...

    def authorization_server_url(self) -> str | None:
        """The issuer that RFC 9728 metadata names; ``None`` when unconfigured."""
        ...

    def authenticate_bearer(self, token: str) -> AuthenticatedUser | None:
        """Resolve a bearer token this server issued (only if ``issues_tokens``)."""
        ...

    def revoke_user_grants(self, user_id: str) -> int:
        """Revoke every MCP grant of ``user_id``; return the tokens revoked."""
        ...


class AuthKitAuthorizationServer:
    """WorkOS AuthKit issues MCP tokens; /mcp verifies them as JWTs."""

    issues_tokens = False
    sign_out = ("/dashboard/logout", "Sign out")

    def mount(self, app: FastAPI) -> None:
        return None

    def authorization_server_url(self) -> str | None:
        return authkit_domain()

    def authenticate_bearer(self, token: str) -> AuthenticatedUser | None:
        # AuthKit tokens go through the /mcp middleware's AuthKit/WorkOS chain.
        return None

    def revoke_user_grants(self, user_id: str) -> int:
        # AuthKit-issued tokens cannot be revoked from here.
        return 0


class BuiltinAuthorizationServer:
    """The built-in OAuth 2.1 server (``api_server.oauth``) issues MCP tokens."""

    issues_tokens = True
    sign_out = ("/dashboard/sign-out-everywhere", "Sign out of all apps")

    def mount(self, app: FastAPI) -> None:
        import api_server.oauth as oauth_server  # noqa: PLC0415
        from api_server.oauth import flow  # noqa: PLC0415

        app.include_router(flow.router)
        app.router.routes.extend(oauth_server.create_sdk_routes())

    def authorization_server_url(self) -> str | None:
        from api_server.oauth.flow import issuer_url  # noqa: PLC0415

        return issuer_url()

    def authenticate_bearer(self, token: str) -> AuthenticatedUser | None:
        from api_server.oauth.storage import lookup_access_token  # noqa: PLC0415

        access = lookup_access_token(token)
        if access is None or not access.subject:
            return None
        scopes = resolve_token_scopes(" ".join(access.scopes))
        if scopes is None:
            return None
        return AuthenticatedUser(
            user_id=access.subject, auth_method="oauth", scopes=scopes
        )

    def revoke_user_grants(self, user_id: str) -> int:
        from api_server.oauth.storage import revoke_user_grants  # noqa: PLC0415

        return revoke_user_grants(user_id)


@cache
def _for_mode(mode: str) -> McpAuthorizationServer:
    if mode == "self":
        return BuiltinAuthorizationServer()
    return AuthKitAuthorizationServer()


def authorization_server() -> McpAuthorizationServer:
    """The strategy for the configured ``MCP_AUTH_MODE``.

    Built once per mode value and keyed on the setting itself, so a test that
    patches ``MCP_AUTH_MODE`` gets the matching strategy.
    """
    return _for_mode(global_config.MCP_AUTH_MODE)
