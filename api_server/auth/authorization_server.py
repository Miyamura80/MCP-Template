"""Which authorization server issues the tokens MCP clients send to /mcp.

``MCP_AUTH_MODE`` picks one of two strategies, and every place that behaves
differently per mode asks :func:`authorization_server` instead of reading the
setting itself:

    authkit (default)  WorkOS AuthKit issues MCP tokens; no OAuth endpoints
                       are mounted here.
    self               the built-in server in ``api_server.oauth`` issues,
                       stores and can revoke them; AuthKit only signs users in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, Protocol

import api_server.oauth as oauth_server
from api_server.auth.authkit_auth import authkit_domain, resolve_token_scopes
from api_server.auth.unified_auth import AuthenticatedUser
from api_server.oauth import flow
from api_server.oauth import storage as oauth_storage
from common import global_config

if TYPE_CHECKING:
    from fastapi import FastAPI


class SignOut(NamedTuple):
    """The dashboard's sign-out control."""

    action: str
    label: str


class McpAuthorizationServer(Protocol):
    """The mode-specific behavior of the server's MCP authorization."""

    issues_tokens: bool
    """True when this server issues, stores and can revoke MCP tokens."""

    sign_out: SignOut

    def mount(self, app: FastAPI) -> None:
        """Add this server's OAuth endpoints, if it has any, to ``app``."""
        ...

    def authorization_server_url(self) -> str | None:
        """The issuer that RFC 9728 metadata names; ``None`` when unconfigured."""
        ...

    def authorization_server_metadata(self) -> dict:
        """The RFC 8414 document served at this origin's well-known path."""
        ...

    def revoke_user_grants(self, user_id: str) -> int:
        """Revoke every MCP grant of ``user_id``; return the tokens revoked."""
        ...


class AuthKitAuthorizationServer:
    """WorkOS AuthKit issues MCP tokens; /mcp verifies them as JWTs."""

    issues_tokens = False
    sign_out = SignOut("/dashboard/logout", "Sign out")

    def mount(self, app: FastAPI) -> None:
        return None

    def authorization_server_url(self) -> str | None:
        return authkit_domain()

    def authorization_server_metadata(self) -> dict:
        # Imported here: the well-known routes import this module.
        from api_server.routes.well_known import (  # noqa: PLC0415
            fetch_authkit_metadata,
        )

        return fetch_authkit_metadata()

    def revoke_user_grants(self, user_id: str) -> int:
        # AuthKit-issued tokens cannot be revoked from here.
        return 0


class BuiltinAuthorizationServer:
    """The built-in OAuth 2.1 server (``api_server.oauth``) issues MCP tokens."""

    issues_tokens = True
    sign_out = SignOut("/dashboard/sign-out-everywhere", "Sign out of all apps")

    def mount(self, app: FastAPI) -> None:
        app.include_router(flow.router)
        app.router.routes.extend(oauth_server.create_sdk_routes())

    def authorization_server_url(self) -> str | None:
        return flow.issuer_url()

    def authorization_server_metadata(self) -> dict:
        return oauth_server.authorization_server_metadata()

    def authenticate_bearer(self, token: str) -> AuthenticatedUser | None:
        """Resolve a bearer token this server issued."""
        access = oauth_storage.lookup_access_token(token)
        if access is None or not access.subject:
            return None
        scopes = resolve_token_scopes(" ".join(access.scopes))
        if scopes is None:
            return None
        return AuthenticatedUser(
            user_id=access.subject, auth_method="oauth", scopes=scopes
        )

    def revoke_user_grants(self, user_id: str) -> int:
        return oauth_storage.revoke_user_grants(user_id)


_SERVERS: dict[str, McpAuthorizationServer] = {
    "authkit": AuthKitAuthorizationServer(),
    "self": BuiltinAuthorizationServer(),
}


def authorization_server() -> McpAuthorizationServer:
    """The strategy for the configured ``MCP_AUTH_MODE``.

    Looked up on each call, so a test that patches ``MCP_AUTH_MODE`` gets the
    matching strategy. An unknown mode raises ``KeyError``.
    """
    return _SERVERS[global_config.MCP_AUTH_MODE]
