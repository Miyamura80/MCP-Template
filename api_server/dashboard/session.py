"""Human browser sessions for the approval dashboard.

Approving a dangerous call must take a person, so the dashboard never accepts
an agent's credentials (``Authorization``, ``X-API-KEY``). It accepts only a
signed session cookie that a browser gets by signing in with WorkOS:

    GET /dashboard/login?next=/dashboard/approvals/apr_x
        -> WorkOS sign-in (PKCE; state + nonce cookie bind it to this browser)
    GET /dashboard/callback
        -> session cookie, redirect to ``next``

Register ``{base}/dashboard/callback`` as a redirect URI on the WorkOS client.

With WorkOS unset, local development gets a sign-in form that takes any user
id. It refuses to run unless ``DEV_ENV`` is local or dev, so a deployment that
forgot WorkOS fails closed instead of letting anyone approve anything.

Mutating requests (form posts, JSON writes) must also carry the session's CSRF
token. Cookies are ``SameSite=Lax`` so a deep link from a chat app arrives
signed in.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import secrets
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, Depends, FastAPI, Form, Query, Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from loguru import logger as log

from api_server.branded_page import render_page
from api_server.oauth.flow import (
    WORKOS_AUTHORIZE_URL,
    authenticate_with_workos,
    issuer_url,
    pkce_challenge,
)
from common import global_config

router = APIRouter(prefix="/dashboard", tags=["dashboard"], include_in_schema=False)

_SESSION_SALT = "mymcp.dashboard.session"
_LOGIN_SALT = "mymcp.dashboard.login"
_LOGIN_MAX_AGE = 600


@dataclass(frozen=True)
class DashboardSession:
    user_id: str
    csrf: str


def _secure() -> bool:
    return not global_config.is_dev


def _cookie(name: str) -> str:
    return f"{'__Host-' if _secure() else ''}mymcp_dash_{name}"


def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        global_config.SESSION_SECRET_KEY,
        salt=salt,
        signer_kwargs={"digest_method": hashlib.sha256},
    )


def workos_enabled() -> bool:
    return bool(global_config.WORKOS_CLIENT_ID and global_config.WORKOS_API_KEY)


def current_session(request: Request) -> DashboardSession | None:
    raw = request.cookies.get(_cookie("session"))
    if not raw:
        return None
    try:
        data = _serializer(_SESSION_SALT).loads(
            raw, max_age=global_config.approvals.dashboard_session_seconds
        )
    except BadSignature:
        return None
    if not isinstance(data, dict) or not data.get("uid") or not data.get("csrf"):
        return None
    return DashboardSession(user_id=str(data["uid"]), csrf=str(data["csrf"]))


def csrf_ok(session: DashboardSession, presented: str | None) -> bool:
    return bool(presented) and hmac.compare_digest(
        session.csrf.encode(), str(presented).encode()
    )


def safe_next(target: str | None) -> str:
    """Only same-site dashboard paths; anything else is an open redirect.

    Judged on the path alone, so a deep link's query (which may contain any
    text, ``//`` included) survives sign-in.
    """
    if target:
        parts = urlsplit(target)
        path = parts.path
        if (
            not parts.scheme
            and not parts.netloc
            and (path == "/dashboard" or path.startswith("/dashboard/"))
            and "//" not in path
            and "\\" not in target
        ):
            return target
    return "/dashboard/approvals"


class _SignInRequired(Exception):  # noqa: N818 - control flow, not an error report
    def __init__(self, request: Request) -> None:
        self.request = request


class _CsrfRejected(Exception):  # noqa: N818 - control flow, not an error report
    pass


def require_session(request: Request) -> DashboardSession:
    """Dependency: the signed-in human, or a redirect to sign in (query kept)."""
    session = current_session(request)
    if session is None:
        raise _SignInRequired(request)
    return session


def require_csrf(
    session: Annotated[DashboardSession, Depends(require_session)],
    csrf: Annotated[str, Form()] = "",
) -> DashboardSession:
    """Dependency for form POSTs: the session, and its CSRF token in the form."""
    if not csrf_ok(session, csrf):
        raise _CsrfRejected
    return session


# Route-parameter types: the signed-in human, and the same for form POSTs
# (CSRF token checked). Named apart from SQLAlchemy's ``Session``.
DashboardUser = Annotated[DashboardSession, Depends(require_session)]
DashboardForm = Annotated[DashboardSession, Depends(require_csrf)]


def install(app: FastAPI) -> None:
    """Turn the two dependencies' refusals into a redirect and a 403 page."""

    async def sign_in(_request: Request, exc: Exception) -> Response:
        assert isinstance(exc, _SignInRequired)  # noqa: S101 - registered for it
        return login_redirect(exc.request)

    async def stale(_request: Request, _exc: Exception) -> Response:
        return render_page(
            "Dashboard",
            "<span class='eyebrow'>Dashboard</span><h1>Request expired</h1>"
            "<p class='warn'>This form is out of date. Reload the page and retry.</p>",
            403,
        )

    app.add_exception_handler(_SignInRequired, sign_in)
    app.add_exception_handler(_CsrfRejected, stale)


def login_redirect(request: Request) -> RedirectResponse:
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(
        f"/dashboard/login?{urlencode({'next': target})}", status_code=303
    )


def _start_session(user_id: str, next_path: str) -> RedirectResponse:
    token = _serializer(_SESSION_SALT).dumps(
        {"uid": user_id, "csrf": secrets.token_urlsafe(24)}
    )
    response = RedirectResponse(safe_next(next_path), status_code=303)
    response.set_cookie(
        _cookie("session"),
        token,
        max_age=global_config.approvals.dashboard_session_seconds,
        path="/",
        secure=_secure(),
        httponly=True,
        samesite="lax",
    )
    return response


def _error(message: str, status_code: int = 400) -> Response:
    return render_page(
        "Dashboard",
        "<span class='eyebrow'>Dashboard</span><h1>Can't sign in</h1>"
        f"<p class='warn'>{html.escape(message)}</p>",
        status_code=status_code,
    )


def _pkce_verifier(nonce: str) -> str:
    digest = hmac.new(
        global_config.SESSION_SECRET_KEY.encode(),
        b"dashboard-pkce:" + nonce.encode(),
        hashlib.sha256,
    ).hexdigest()
    return digest  # 64 hex chars: a valid RFC 7636 verifier


@router.get("/login")
def login(
    request: Request, next_path: str = Query(default="", alias="next")
) -> Response:
    target = safe_next(next_path)
    if workos_enabled():
        nonce = secrets.token_urlsafe(24)
        state = _serializer(_LOGIN_SALT).dumps({"next": target, "nonce": nonce})
        query = urlencode(
            {
                "response_type": "code",
                "client_id": global_config.WORKOS_CLIENT_ID or "",
                "redirect_uri": f"{issuer_url()}/dashboard/callback",
                "provider": "authkit",
                "state": state,
                "code_challenge": pkce_challenge(_pkce_verifier(nonce)),
                "code_challenge_method": "S256",
            }
        )
        response = RedirectResponse(f"{WORKOS_AUTHORIZE_URL}?{query}", status_code=303)
        response.set_cookie(
            _cookie("login"),
            nonce,
            max_age=_LOGIN_MAX_AGE,
            path="/",
            secure=_secure(),
            httponly=True,
            samesite="lax",
        )
        return response
    if not global_config.is_dev:
        return _error(
            "Dashboard sign-in needs WorkOS (WORKOS_CLIENT_ID and WORKOS_API_KEY).",
            status_code=503,
        )
    return render_page(
        "Dashboard sign-in (dev)",
        "<span class='eyebrow'>Dashboard</span><h1>Sign in</h1>"
        "<section class='box'><span class='tag'>Development</span>"
        "<p class='warn'>WorkOS is not configured, so you can sign in as any user "
        "id. This form is refused outside DEV_ENV=local/dev.</p>"
        "<form method='post' action='/dashboard/login'>"
        f"<input type='hidden' name='next' value='{html.escape(target, quote=True)}'>"
        "<label class='field'><span>User id</span><input name='user_id' required>"
        "</label><div class='actions'>"
        "<button class='primary' type='submit'>Sign in</button></div></form>"
        "</section>",
    )


@router.post("/login")
def dev_login(user_id: str = Form(), next_path: str = Form(default="", alias="next")):
    if workos_enabled() or not global_config.is_dev:
        return _error("Development sign-in is disabled here.", status_code=403)
    user_id = user_id.strip()
    if not user_id:
        return _error("Enter a user id.")
    return _start_session(user_id, next_path)


@router.get("/callback")
def callback(
    request: Request,
    state: str = Query(default=""),
    code: str | None = Query(default=None),
) -> Response:
    try:
        data = _serializer(_LOGIN_SALT).loads(state, max_age=_LOGIN_MAX_AGE)
    except BadSignature:
        return _error("This sign-in link is invalid or has expired.")
    nonce = request.cookies.get(_cookie("login"))
    if (
        not isinstance(data, dict)
        or not nonce
        or not hmac.compare_digest(nonce.encode(), str(data.get("nonce", "")).encode())
    ):
        return _error("Sign-in must finish in the browser where it started.")
    if not code:
        return _error("Sign-in was cancelled.")
    try:
        user_id = authenticate_with_workos(code, _pkce_verifier(nonce))
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        log.warning("dashboard: WorkOS code exchange failed: {}", type(exc).__name__)
        return _error("Sign-in failed. Try again.", status_code=502)
    response = _start_session(user_id, str(data.get("next", "")))
    response.delete_cookie(_cookie("login"), path="/", secure=_secure(), httponly=True)
    return response


@router.post("/logout")
def logout(request: Request, csrf: str = Form(default="")) -> Response:
    session = current_session(request)
    response = RedirectResponse("/dashboard/login", status_code=303)
    if session is not None and csrf_ok(session, csrf):
        response.delete_cookie(
            _cookie("session"), path="/", secure=_secure(), httponly=True
        )
    return response
