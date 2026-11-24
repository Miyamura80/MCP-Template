"""API key pages on the dashboard, deep-linkable so an agent can hand you one.

    /dashboard/settings/api-keys                          list, revoke
    /dashboard/settings/api-keys/create?name=&expiration=&scope=
        a create form, prefilled from the query; you still click Create

Agents shouldn't mint credentials themselves, so instead of a tool they give
the user a link like
``/dashboard/settings/api-keys/create?name=ci-bot&expiration=30d&scope=read_only``
(see :func:`create_link`). Opening the link never creates anything: GET only
prefills the form, and the POST needs the signed-in human's CSRF token. The
new key is shown once, on the response to that POST, and never sits in a URL.

Query parameters (all optional; a bad value is ignored with a note on the page):

- ``name``: up to 100 characters.
- ``expiration``: ``none``, or a number of days with a ``d`` suffix (``30d``),
  1 to 365.
- ``scope``: a scope template, one of ``read_only``, ``standard``, ``admin``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import RedirectResponse, Response

from api_server.auth.api_key_auth import create_api_key, revoke_api_key
from api_server.auth.scopes import SCOPE_TEMPLATES, validate_scopes
from api_server.dashboard.session import csrf_ok, current_session, login_redirect
from api_server.dashboard.ui import csrf_field, esc, nav
from api_server.oauth.flow import issuer_url, render_page
from db.engine import use_db_session
from db.models.api_keys import APIKey

router = APIRouter(
    prefix="/dashboard/settings/api-keys", tags=["dashboard"], include_in_schema=False
)

_MAX_NAME = 100
_MAX_DAYS = 365
_DEFAULT_SCOPE = "standard"
_EXPIRATION = re.compile(r"^(none|([1-9][0-9]{0,2})d)$")
_SCOPE_HELP = {
    "read_only": "read tools, billing and payments",
    "standard": "run tools, billing and payments",
    "admin": "everything, including admin",
}


def create_link(
    name: str | None = None, expiration: str | None = None, scope: str | None = None
) -> str:
    """The deep link an agent (or docs, or a CLI) gives a user to make a key."""
    query = {
        k: v
        for k, v in (("name", name), ("expiration", expiration), ("scope", scope))
        if v
    }
    base = f"{issuer_url()}/dashboard/settings/api-keys/create"
    return f"{base}?{urlencode(query)}" if query else base


def _parse_expiration(raw: str | None) -> tuple[str, int | None, str | None]:
    """``(form value, days or None, problem)`` for an ``expiration`` value."""
    if not raw:
        return "none", None, None
    match = _EXPIRATION.match(raw.strip().lower())
    if match is None or (match.group(2) and int(match.group(2)) > _MAX_DAYS):
        return (
            "none",
            None,
            (f"Ignored expiration {raw!r}: use none or 1d to {_MAX_DAYS}d."),
        )
    if match.group(1) == "none":
        return "none", None, None
    return match.group(1), int(match.group(2)), None


def _parse_scope(raw: str | None) -> tuple[str, str | None]:
    if not raw:
        return _DEFAULT_SCOPE, None
    if raw not in SCOPE_TEMPLATES:
        return _DEFAULT_SCOPE, (
            f"Ignored scope {raw!r}: use one of {', '.join(SCOPE_TEMPLATES)}."
        )
    return raw, None


def _parse_name(raw: str | None) -> tuple[str, str | None]:
    name = (raw or "").strip()
    if len(name) > _MAX_NAME:
        return name[:_MAX_NAME], f"Name shortened to {_MAX_NAME} characters."
    return name, None


def _form(session, name: str, expiration: str, scope: str, notes: list[str]) -> str:
    warn = "".join(f"<p class='warn'>{esc(n)}</p>" for n in notes)
    expirations = ["none", "7d", "30d", "90d", "365d"]
    if expiration not in expirations:
        expirations.insert(1, expiration)
    expiry_opts = "".join(
        f"<option value='{esc(e)}'{' selected' if e == expiration else ''}>"
        f"{'Never' if e == 'none' else e.removesuffix('d') + ' days'}</option>"
        for e in expirations
    )
    scope_opts = "".join(
        f"<option value='{esc(t)}'{' selected' if t == scope else ''}>"
        f"{esc(t)}: {esc(_SCOPE_HELP.get(t, ''))}</option>"
        for t in SCOPE_TEMPLATES
    )
    return (
        "<h1>Create an API key</h1>"
        + nav(session)
        + warn
        + "<form method='post' action='/dashboard/settings/api-keys/create'>"
        + csrf_field(session)
        + "<p><label>Name<br><input name='name' required maxlength='100' "
        f"value='{esc(name)}' style='width:100%'></label></p>"
        f"<p><label>Expires<br><select name='expiration'>{expiry_opts}</select>"
        "</label></p>"
        f"<p><label>Access<br><select name='scope'>{scope_opts}</select></label></p>"
        "<button class='allow' type='submit'>Create key</button>"
        "<a href='/dashboard/settings/api-keys'>Cancel</a></form>"
    )


@router.get("/create")
def create_page(
    request: Request,
    name: Annotated[str | None, Query()] = None,
    expiration: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query()] = None,
) -> Response:
    session = current_session(request)
    if session is None:
        return login_redirect(request)  # keeps the query, so the prefill survives
    name_value, name_note = _parse_name(name)
    expiry_value, _days, expiry_note = _parse_expiration(expiration)
    scope_value, scope_note = _parse_scope(scope)
    notes = [n for n in (name_note, expiry_note, scope_note) if n]
    return render_page(
        "Create an API key",
        _form(session, name_value, expiry_value, scope_value, notes),
    )


@router.post("/create")
def create_submit(
    request: Request,
    name: Annotated[str, Form()],
    expiration: Annotated[str, Form()] = "none",
    scope: Annotated[str, Form()] = _DEFAULT_SCOPE,
    csrf: Annotated[str, Form()] = "",
) -> Response:
    session = current_session(request)
    if session is None:
        return login_redirect(request)
    if not csrf_ok(session, csrf):
        return render_page(
            "API keys", "<h1>Request expired</h1><p>Reload and retry.</p>", 403
        )
    name_value, _ = _parse_name(name)
    expiry_value, days, expiry_note = _parse_expiration(expiration)
    scope_value, scope_note = _parse_scope(scope)
    if not name_value or expiry_note or scope_note:
        notes = [n for n in (expiry_note, scope_note) if n] or ["Enter a name."]
        return render_page(
            "Create an API key",
            _form(session, name_value, expiry_value, scope_value, notes),
            status_code=400,
        )
    with use_db_session() as db:
        raw_key, row = create_api_key(
            db,
            user_id=session.user_id,
            name=name_value,
            expires_in_days=days,
            scopes=validate_scopes([scope_value]),
        )
        expires = row.expires_at.date().isoformat() if row.expires_at else "never"
    return render_page(
        "API key created",
        "<h1>API key created</h1>"
        + nav(session)
        + f"<p><strong>{esc(name_value)}</strong> · {esc(scope_value)} · "
        f"expires {esc(expires)}</p>"
        "<p class='warn'>Copy it now. It is shown only once.</p>"
        f"<p><code style='font-size:1.05rem'>{esc(raw_key)}</code></p>"
        "<p>Send it as the <code>X-API-KEY</code> header.</p>"
        "<p><a href='/dashboard/settings/api-keys'>Back to API keys</a></p>",
    )


@router.get("")
def list_page(request: Request) -> Response:
    session = current_session(request)
    if session is None:
        return login_redirect(request)
    with use_db_session() as db:
        rows = (
            db.query(APIKey)
            .filter(APIKey.user_id == session.user_id, APIKey.revoked.is_(False))
            .order_by(APIKey.created_at.desc())
            .all()
        )
        now = datetime.now(UTC)
        items = "".join(_row(session, r, now) for r in rows)
    return render_page(
        "API keys",
        "<h1>API keys</h1>"
        + nav(session)
        + "<p><a href='/dashboard/settings/api-keys/create'>Create an API key</a></p>"
        + (f"<ul>{items}</ul>" if items else "<p>No keys yet.</p>"),
    )


def _row(session, row: APIKey, now: datetime) -> str:
    expires = row.expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    state = (
        "expired"
        if expires is not None and expires < now
        else f"expires {expires.date().isoformat()}"
        if expires
        else "no expiry"
    )
    return (
        f"<li><strong>{esc(row.name)}</strong> <code>{esc(row.key_prefix)}…</code> · "
        f"{esc(state)}"
        f"<form method='post' action='/dashboard/settings/api-keys/{row.id}/revoke' "
        "style='display:inline'>"
        + csrf_field(session)
        + "<button type='submit'>Revoke</button></form></li>"
    )


@router.post("/{key_id}/revoke")
def revoke(
    request: Request, key_id: int, csrf: Annotated[str, Form()] = ""
) -> Response:
    session = current_session(request)
    if session is None:
        return login_redirect(request)
    if not csrf_ok(session, csrf):
        return render_page(
            "API keys", "<h1>Request expired</h1><p>Reload and retry.</p>", 403
        )
    with use_db_session() as db:
        revoke_api_key(db, key_id=key_id, user_id=session.user_id)
    return RedirectResponse("/dashboard/settings/api-keys", status_code=303)
