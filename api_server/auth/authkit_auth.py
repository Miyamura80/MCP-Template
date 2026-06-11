"""AuthKit (WorkOS) OAuth 2.1 access-token verification for the /mcp transport.

Per the MCP authorization spec (2025-11-25) the MCP server is an OAuth 2.1
*resource server*: AuthKit acts as the authorization server (client
registration via CIMD/DCR, PKCE, consent) and issues RS256 access tokens
audience-bound to this server's canonical resource URI (RFC 8707). This module
verifies signature, issuer, and audience of those tokens.

Enabled by setting ``WORKOS_AUTHKIT_DOMAIN`` (the AuthKit issuer URL, e.g.
``https://your-env.authkit.app``). ``MCP_PUBLIC_URL`` must match the resource
indicator configured in the WorkOS dashboard.
"""

from urllib.parse import urlsplit

import jwt
from jwt import PyJWKClient

from common import global_config

_jwks_clients: dict[str, PyJWKClient] = {}


def authkit_domain() -> str | None:
    """Return the configured AuthKit issuer URL without a trailing slash."""
    domain = global_config.WORKOS_AUTHKIT_DOMAIN
    return domain.rstrip("/") if domain else None


def mcp_resource_url() -> str:
    """Canonical RFC 8707 resource URI of the /mcp endpoint.

    Tokens are audience-bound to this exact string, so in any deployed
    environment ``MCP_PUBLIC_URL`` must be set to the public URL clients
    connect to (no trailing slash, per the MCP spec's canonical-URI guidance).
    """
    configured = global_config.MCP_PUBLIC_URL
    if configured:
        return configured.rstrip("/")
    return f"http://localhost:{global_config.server.port}/mcp"


def resource_metadata_url() -> str:
    """RFC 9728 path-form well-known URL for the resource above."""
    parts = urlsplit(mcp_resource_url())
    origin = f"{parts.scheme}://{parts.netloc}"
    return f"{origin}/.well-known/oauth-protected-resource{parts.path}"


def _get_jwks_client(domain: str) -> PyJWKClient:
    if domain not in _jwks_clients:
        _jwks_clients[domain] = PyJWKClient(f"{domain}/oauth2/jwks")
    return _jwks_clients[domain]


class AuthKitUser:
    """Minimal representation of a user verified from an AuthKit access token."""

    def __init__(
        self, user_id: str, email: str | None = None, scopes: list[str] | None = None
    ):
        self.user_id = user_id
        self.email = email
        self.scopes = scopes


def verify_authkit_token(token: str) -> AuthKitUser | None:
    """Verify an AuthKit-issued access token; return the user or ``None``."""
    domain = authkit_domain()
    if not domain:
        return None

    try:
        signing_key = _get_jwks_client(domain).get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=mcp_resource_url(),
            issuer=[domain, domain + "/"],
        )
    except jwt.PyJWTError:
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    if "scope" in payload:
        raw = payload["scope"]
        if isinstance(raw, str):
            scopes = raw.split()
        elif isinstance(raw, list):
            scopes = [str(s) for s in raw]
        else:
            # Malformed scope claim: reject the token instead of erroring.
            return None
    else:
        # No scope claim: the user authorized the whole resource, mirroring
        # how unified_auth treats interactive JWT users.
        scopes = ["*"]

    return AuthKitUser(user_id=user_id, email=payload.get("email"), scopes=scopes)
