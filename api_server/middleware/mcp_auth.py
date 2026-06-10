"""Authentication for the streamable-HTTP /mcp endpoint.

Reuses the same Bearer-JWT / API-key flow as the REST API. Implemented as pure
ASGI middleware (not :class:`BaseHTTPMiddleware`) so it doesn't buffer the
streaming SSE responses FastMCP emits.

OAuth 2.1 dynamic client registration (the MCP-spec-preferred flow) is tracked
separately; this middleware accepts the same credentials a REST client would.
"""

import json

from starlette.types import ASGIApp, Receive, Scope, Send

from api_server.auth.api_key_auth import validate_api_key
from api_server.auth.unified_auth import AuthenticatedUser
from api_server.auth.workos_auth import verify_workos_token
from common import global_config
from db.engine import use_db_session
from src.utils.current_user import reset_current_user, set_current_user

_MCP_PATH_PREFIX = "/mcp"


class MCPAuthMiddleware:
    """Authenticate requests to /mcp and bind the user to a ContextVar."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(_MCP_PATH_PREFIX):
            await self.app(scope, receive, send)
            return

        user = await _authenticate_async(scope)
        if user is None:
            await _send_unauthorized(send)
            return

        token = set_current_user(user)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_current_user(token)


async def _authenticate_async(scope: Scope) -> AuthenticatedUser | None:
    """Run blocking auth I/O in a thread to avoid blocking the event loop."""
    import anyio

    return await anyio.to_thread.run_sync(lambda: _authenticate(scope))  # ty: ignore[unresolved-attribute]


def _authenticate(scope: Scope) -> AuthenticatedUser | None:
    headers = {
        k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]
    }

    auth_header = headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()
        workos_user = verify_workos_token(token)
        if workos_user:
            return AuthenticatedUser(
                user_id=workos_user.user_id,
                email=workos_user.email,
                auth_method="jwt",
                scopes=["*"],
            )
        if global_config.WORKOS_CLIENT_ID:
            return None

    api_key = headers.get("x-api-key", "")
    if api_key:
        with use_db_session() as session:
            row = validate_api_key(session, api_key)
            if row:
                return AuthenticatedUser(
                    user_id=row.user_id,
                    auth_method="api_key",
                    scopes=row.scopes,
                )

    return None


async def _send_unauthorized(send: Send) -> None:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "error": {"code": -32001, "message": "Authentication required"},
            "id": None,
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b'Bearer realm="mcp"'),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
