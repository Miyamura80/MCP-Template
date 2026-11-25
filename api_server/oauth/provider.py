"""MCP SDK provider for the built-in OAuth 2.1 authorization server.

This server issues, stores and revokes the tokens MCP clients use, so signing
out can invalidate them. WorkOS AuthKit still performs the sign-in: the
authorize step sends the user to a consent page and then to WorkOS, and the
WorkOS callback (``api_server/oauth/flow.py``) mints the authorization code
this provider later exchanges.

Token handling, per OAuth 2.1 and the MCP authorization spec (2026-07-28):

* Codes and tokens are random, opaque, and stored only as SHA-256 hashes.
* Access tokens live for one hour and are bound to this server's canonical MCP
  URL (RFC 8707); a request for any other resource is refused.
* Refresh tokens are single-use. Each refresh rotates to a new token in the same
  family; presenting a used refresh token revokes the whole family. A family
  ends 90 days after sign-in however often it rotates, and a refresh can never
  widen what the token may do.
* A code can be exchanged once; a replayed code revokes what it issued.
* After a user's grants are revoked (``storage.revoke_user_grants``), no code or
  sign-in that began earlier yields tokens.

Everything that issues a code or tokens, and sign-out itself, first takes a
per-user lock (``db.models.oauth.lock_user``), so a sign-out cannot interleave
with an exchange or refresh and leave a token it did not see.

Every transaction is a public synchronous function in ``storage``; this class
only validates the authorize request and runs those off the event loop.
"""

import re

from anyio import to_thread
from loguru import logger as log
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
)
from mcp.shared.auth import OAuthClientInformationFull
from mcp.shared.auth import OAuthToken as TokenResponse

from api_server.auth.authkit_auth import mcp_resource_url
from api_server.oauth import cimd, signing, storage

# RFC 7636: an S256 challenge is a SHA-256 digest, base64url without padding.
_CODE_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")


# ---------------------------------------------------------------------------
# SDK provider
# ---------------------------------------------------------------------------


class BuiltinOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Plugs our token storage and WorkOS sign-in into the MCP SDK's OAuth routes."""

    def __init__(self, issuer_url: str) -> None:
        self.issuer_url = issuer_url.rstrip("/")

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        try:
            return await cimd.resolve_client(client_id)
        except cimd.ClientMetadataError as exc:
            log.warning("oauth: rejected client metadata document: {}", exc)
            return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # Dynamic Client Registration is deprecated in the MCP spec (2026-07-28);
        # this server only accepts Client ID Metadata Documents.
        raise RegistrationError(
            error="invalid_client_metadata",
            error_description="Use a Client ID Metadata Document URL as client_id.",
        )

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        # Bind the token to this server (RFC 8707). Clients must send resource;
        # when one is absent, the token is still bound to this server.
        resource = params.resource or mcp_resource_url()
        if not storage.resource_matches(resource):
            raise AuthorizeError(
                error="invalid_request",
                error_description="resource must be this MCP server's URL",
            )
        if not _CODE_CHALLENGE.fullmatch(params.code_challenge):
            raise AuthorizeError(
                error="invalid_request",
                error_description="code_challenge must be an RFC 7636 S256 challenge",
            )
        pending = signing.sign_pending_authorization(
            {
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "code_challenge": params.code_challenge,
                "state": params.state,
                "scopes": params.scopes or [],
                # Store the bare URL: the query was connection flags.
                "resource": mcp_resource_url(),
            }
        )
        # Consent comes first: the user must see which client is asking before
        # this server forwards to WorkOS (MCP spec, confused deputy).
        return f"{self.issuer_url}/oauth/consent?request={pending}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return await to_thread.run_sync(
            storage.load_authorization_code, client.client_id, authorization_code
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> TokenResponse:
        return await to_thread.run_sync(
            storage.exchange_authorization_code, authorization_code.code
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        return await to_thread.run_sync(
            storage.load_refresh_token, client.client_id, refresh_token
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> TokenResponse:
        return await to_thread.run_sync(
            storage.exchange_refresh_token, refresh_token.token, scopes
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        return await to_thread.run_sync(storage.lookup_access_token, token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await to_thread.run_sync(storage.revoke_token, token.token)
