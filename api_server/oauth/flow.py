"""Browser leg of the built-in OAuth server: consent page and WorkOS sign-in.

    /authorize (MCP SDK) ──► GET  /oauth/consent      shows which client is asking
                             POST /oauth/consent      Allow ──► WorkOS sign-in
    WorkOS ────────────────► GET  /oauth/workos/callback
                             ──► client redirect_uri?code=..&state=..&iss=..

Consent is required before forwarding to WorkOS: this server reaches WorkOS
with one static client id, and WorkOS signs a returning user in without a
prompt, so without this page any client could obtain a code for a signed-in
user (MCP authorization spec 2026-07-28, Security Considerations: confused
deputy). The page names the client and shows the redirect hostname, and warns
when the redirect is a loopback address (same section, Localhost Redirect URI
Risks).

Each sign-in is bound to the browser that started it. The consent page sets a
cookie named for that sign-in, holding a random nonce; the consent form and the
WorkOS state carry the same nonce, so a cross-site form post or a callback
replayed in another browser is refused. In production the cookie uses the
``__Host-`` prefix, so no other subdomain can plant or overwrite it. The hop to
WorkOS also uses PKCE, with a verifier derived from the nonce and the server
secret, so a leaked WorkOS code cannot be redeemed through another sign-in.

Until the pending request has been verified, errors render a page instead of
redirecting anywhere.
"""

import base64
import hashlib
import hmac
import html
import re
import secrets
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from loguru import logger as log
from mcp.server.auth.provider import construct_redirect_uri

from api_server.branded_page import render_page
from api_server.oauth import cimd, signing
from api_server.oauth.storage import store_authorization_code
from common import global_config

WORKOS_AUTHORIZE_URL = "https://api.workos.com/user_management/authorize"
WORKOS_AUTHENTICATE_URL = "https://api.workos.com/user_management/authenticate"
_WORKOS_TIMEOUT_SECONDS = 10.0
_FLOW_ID = re.compile(r"[0-9a-f]{16}")
# Pages come from api_server.branded_page: no script, and frame-ancestors
# stops the Allow button from being framed (clickjacking).

router = APIRouter(prefix="/oauth", tags=["oauth"])


def issuer_url() -> str:
    """This authorization server's issuer: the origin of the public MCP URL."""
    from api_server.auth.authkit_auth import mcp_resource_url  # noqa: PLC0415

    parts = urlsplit(mcp_resource_url())
    return f"{parts.scheme}://{parts.netloc}"


def _callback_url() -> str:
    return f"{issuer_url()}/oauth/workos/callback"


def _secure_cookies() -> bool:
    return not global_config.is_dev


def cookie_name(flow_id: str) -> str:
    # __Host- requires Secure, Path=/ and no Domain, so it is production-only;
    # local development is served over plain http.
    prefix = "__Host-" if _secure_cookies() else ""
    return f"{prefix}mymcp_oauth_{flow_id}"


def _workos_pkce_verifier(nonce: str) -> str:
    """The PKCE verifier for the WorkOS hop, recomputable only by this server."""
    digest = hmac.new(
        global_config.SESSION_SECRET_KEY.encode(),
        b"workos-pkce:" + nonce.encode(),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def pkce_challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )


def _error_page(message: str) -> HTMLResponse:
    return render_page(
        "Sign-in failed",
        "<span class='eyebrow'>Sign-in</span><h1>Sign-in failed</h1>"
        f"<p class='warn'>{html.escape(message)}</p>"
        "<p class='lead'>Return to your app and connect again.</p>",
        status_code=400,
    )


def _redirect_to_client(
    pending: dict, flow_id: str, **params: str | None
) -> RedirectResponse:
    """Send the browser back to the client, with ``iss`` (RFC 9207) and its state."""
    location = construct_redirect_uri(
        pending["redirect_uri"], state=pending.get("state"), iss=issuer_url(), **params
    )
    response = RedirectResponse(location, status_code=303)
    response.delete_cookie(
        cookie_name(flow_id), path="/", secure=_secure_cookies(), httponly=True
    )
    return response


def _nonce_matches(request: Request, flow_id: object, presented: object) -> bool:
    if not isinstance(flow_id, str) or not _FLOW_ID.fullmatch(flow_id):
        return False
    if not isinstance(presented, str):
        return False
    cookie = request.cookies.get(cookie_name(flow_id))
    # Bytes, so a non-ASCII value fails the comparison instead of raising.
    return bool(cookie) and hmac.compare_digest(cookie.encode(), presented.encode())


@router.get("/consent", include_in_schema=False)
async def consent_page(
    request: Request, request_token: str = Query(alias="request")
) -> Response:
    try:
        pending = signing.load_pending_authorization(request_token)
        client = await cimd.resolve_client(pending["client_id"])
    except (signing.InvalidSignedValueError, cimd.ClientMetadataError, KeyError):
        return _error_page("This sign-in link is invalid or has expired.")

    client_name = (
        client.client_name or urlsplit(pending["client_id"]).hostname or "An app"
    )
    client_host = urlsplit(pending["client_id"]).hostname or ""
    redirect_host = urlsplit(pending["redirect_uri"]).hostname or ""
    warning = ""
    if cimd.is_loopback_redirect(pending["redirect_uri"]):
        warning = (
            "<p class='warn'>This app sends you back to a program on this computer "
            f"(<code>{html.escape(redirect_host)}</code>). Only continue if you started "
            "this from an app you installed and trust.</p>"
        )
    flow_id = secrets.token_hex(8)
    nonce = secrets.token_urlsafe(24)
    server_name = html.escape(global_config.branding.title)
    response = render_page(
        f"Connect to {global_config.branding.title}",
        "<span class='eyebrow'>Authorize</span>"
        f"<h1>Connect to {server_name}</h1>"
        "<div class='box'><span class='tag'>Access request</span>"
        f"<p><strong>{html.escape(client_name)}</strong> "
        f"(<code>{html.escape(client_host)}</code>) wants to access your "
        f"{server_name} account.</p>"
        f"<p>After you sign in, you will be sent to <code>{html.escape(redirect_host)}</code>.</p>"
        f"{warning}"
        "<form method='post' action='/oauth/consent' class='actions'>"
        f"<input type='hidden' name='request' value='{html.escape(request_token, quote=True)}'>"
        f"<input type='hidden' name='flow' value='{flow_id}'>"
        f"<input type='hidden' name='nonce' value='{html.escape(nonce, quote=True)}'>"
        "<button class='allow' name='decision' value='allow' type='submit'>Allow</button>"
        "<button name='decision' value='deny' type='submit'>Deny</button>"
        "</form></div>",
    )
    response.set_cookie(
        cookie_name(flow_id),
        nonce,
        max_age=signing.MAX_AGE_SECONDS,
        path="/",
        secure=_secure_cookies(),
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/consent", include_in_schema=False)
def consent_decision(
    request: Request,
    request_token: str = Form(alias="request"),
    flow: str = Form(),
    nonce: str = Form(),
    decision: str = Form(),
) -> Response:
    try:
        pending = signing.load_pending_authorization(request_token)
    except signing.InvalidSignedValueError:
        return _error_page("This sign-in link is invalid or has expired.")
    if not _nonce_matches(request, flow, nonce):
        return _error_page("This request did not come from the consent page.")
    if decision != "allow":
        return _redirect_to_client(pending, flow, error="access_denied")

    state = signing.sign_workos_state({**pending, "flow": flow, "nonce": nonce})
    query = urlencode(
        {
            "response_type": "code",
            "client_id": global_config.WORKOS_CLIENT_ID or "",
            "redirect_uri": _callback_url(),
            "provider": "authkit",
            "state": state,
            "code_challenge": pkce_challenge(_workos_pkce_verifier(nonce)),
            "code_challenge_method": "S256",
        }
    )
    return RedirectResponse(f"{WORKOS_AUTHORIZE_URL}?{query}", status_code=303)


def authenticate_with_workos(code: str, code_verifier: str) -> str:
    """Exchange a WorkOS code for the signed-in user's id."""
    response = httpx.post(
        WORKOS_AUTHENTICATE_URL,
        json={
            "client_id": global_config.WORKOS_CLIENT_ID,
            "client_secret": global_config.WORKOS_API_KEY,
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
        },
        timeout=_WORKOS_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    user_id = response.json()["user"]["id"]
    if not isinstance(user_id, str) or not user_id:
        raise ValueError("WorkOS returned no user id")
    return user_id


@router.get("/workos/callback", include_in_schema=False)
def workos_callback(
    request: Request,
    state: str = Query(default=""),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> Response:
    try:
        pending, allowed_at = signing.load_workos_state(state)
    except signing.InvalidSignedValueError:
        return _error_page("This sign-in link is invalid or has expired.")
    flow_id = pending.get("flow")
    if not _nonce_matches(request, flow_id, pending.get("nonce")):
        return _error_page("Sign-in must finish in the browser where it started.")
    flow_id = str(flow_id)  # a 16-hex-digit string, checked by _nonce_matches
    if error or not code:
        return _redirect_to_client(pending, flow_id, error="access_denied")

    try:
        user_id = authenticate_with_workos(
            code, _workos_pkce_verifier(pending["nonce"])
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        log.warning("oauth: WorkOS code exchange failed: {}", type(exc).__name__)
        return _redirect_to_client(pending, flow_id, error="server_error")

    our_code = store_authorization_code(
        client_id=pending["client_id"],
        user_id=user_id,
        redirect_uri=pending["redirect_uri"],
        redirect_uri_provided_explicitly=bool(
            pending["redirect_uri_provided_explicitly"]
        ),
        code_challenge=pending["code_challenge"],
        scopes=list(pending.get("scopes") or []),
        resource=pending["resource"],
        started_at=allowed_at,
    )
    if our_code is None:
        # The user's grants were revoked while this sign-in was under way.
        return _redirect_to_client(pending, flow_id, error="access_denied")
    return _redirect_to_client(pending, flow_id, code=our_code)
