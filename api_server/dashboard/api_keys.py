"""API key pages on the dashboard, deep-linkable so an agent can hand you one.

    /dashboard/settings/api-keys                          list, revoke
    /dashboard/settings/api-keys/create?name=&expiration=&scope=
        a create form, prefilled from the query; you still click Create

Agents shouldn't mint credentials themselves, so instead of a tool they give
the user a link like
``/dashboard/settings/api-keys/create?name=ci-bot&expiration=30d&scope=read_only``.
Opening the link never creates anything: GET only prefills the form, and the
POST needs the signed-in human's CSRF token. The new key is shown once, on the
response to that POST, and never sits in a URL.

Query parameters (all optional; a value the page can't use is dropped, with a
note saying so):

- ``name``: up to 100 characters.
- ``expiration``: ``none``, or a number of days with a ``d`` suffix (``30d``),
  1 to 365.
- ``scope``: ``read_only`` or ``standard``. ``admin`` is never taken from a
  link: a key with full access is something the person picks themselves.

What makes a valid key is ``models.auth.CreateAPIKeyRequest``, shared with the
REST route; this module only turns the deep link's strings into that request.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query
from fastapi.responses import RedirectResponse, Response
from pydantic import ValidationError

from api_server.auth.api_key_auth import (
    aware_expires_at,
    create_api_key,
    is_expired,
    list_active_keys,
    revoke_api_key,
)
from api_server.auth.scopes import (
    SCOPE_TEMPLATE_HELP,
    SCOPE_TEMPLATES,
    validate_scopes,
)
from api_server.dashboard.session import (
    DashboardSession,
    require_csrf,
    require_session,
)
from api_server.dashboard.ui import csrf_field, esc, nav
from api_server.oauth.flow import render_page
from db.engine import use_db_session
from db.models.api_keys import APIKey
from models.auth import MAX_KEY_DAYS, MAX_KEY_NAME, CreateAPIKeyRequest

router = APIRouter(
    prefix="/dashboard/settings/api-keys", tags=["dashboard"], include_in_schema=False
)

_DEFAULT_SCOPE = "standard"
# Full access is never preselected by a link (see module doc).
_LINKABLE_SCOPES = frozenset(SCOPE_TEMPLATES) - {"admin"}
_EXPIRATION = re.compile(r"^(?:none|([1-9][0-9]{0,2})d)$")
_EXPIRY_CHOICES = ("none", "7d", "30d", "90d", "365d")

Session = Annotated[DashboardSession, Depends(require_session)]
FormSession = Annotated[DashboardSession, Depends(require_csrf)]


def _parse_expiration(raw: str | None) -> tuple[str, int | None, str | None]:
    """``(form value, days or None, problem)`` for an ``expiration`` value."""
    match = _EXPIRATION.match((raw or "none").strip().lower())
    days = int(match.group(1)) if match and match.group(1) else None
    if match is None or (days is not None and days > MAX_KEY_DAYS):
        return (
            "none",
            None,
            (f"Dropped expiration {raw!r}: use none or 1d to {MAX_KEY_DAYS}d."),
        )
    return (f"{days}d" if days else "none"), days, None


def _parse_scope(raw: str | None, allowed: frozenset[str]) -> tuple[str, str | None]:
    if not raw:
        return _DEFAULT_SCOPE, None
    if raw not in allowed:
        hint = (
            "choose admin yourself below"
            if raw == "admin"
            else f"use one of {', '.join(sorted(allowed))}"
        )
        return _DEFAULT_SCOPE, f"Dropped scope {raw!r}: {hint}."
    return raw, None


def _form(
    session: DashboardSession,
    name: str,
    expiration: str,
    scope: str,
    notes: list[str],
) -> str:
    warn = "".join(f"<p class='warn'>{esc(n)}</p>" for n in notes)
    choices = list(_EXPIRY_CHOICES)
    if expiration not in choices:
        choices.insert(1, expiration)
    expiry_opts = "".join(
        f"<option value='{esc(e)}'{' selected' if e == expiration else ''}>"
        f"{'Never' if e == 'none' else e.removesuffix('d') + ' days'}</option>"
        for e in choices
    )
    scope_opts = "".join(
        f"<option value='{esc(t)}'{' selected' if t == scope else ''}>"
        f"{esc(t)}: {esc(SCOPE_TEMPLATE_HELP[t])}</option>"
        for t in SCOPE_TEMPLATES
    )
    return (
        "<h1>Create an API key</h1>"
        + nav(session)
        + warn
        + "<form method='post' action='/dashboard/settings/api-keys/create'>"
        + csrf_field(session)
        + f"<p><label>Name<br><input name='name' required maxlength='{MAX_KEY_NAME}' "
        f"value='{esc(name)}' style='width:100%'></label></p>"
        f"<p><label>Expires<br><select name='expiration'>{expiry_opts}</select>"
        "</label></p>"
        f"<p><label>Access<br><select name='scope'>{scope_opts}</select></label></p>"
        "<button class='allow' type='submit'>Create key</button>"
        "<a href='/dashboard/settings/api-keys'>Cancel</a></form>"
    )


@router.get("/create")
def create_page(
    session: Session,
    name: Annotated[str | None, Query()] = None,
    expiration: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query()] = None,
) -> Response:
    notes: list[str] = []
    name_value = (name or "").strip()
    if len(name_value) > MAX_KEY_NAME:
        name_value = name_value[:MAX_KEY_NAME]
        notes.append(f"Name shortened to {MAX_KEY_NAME} characters.")
    expiry_value, _days, expiry_note = _parse_expiration(expiration)
    scope_value, scope_note = _parse_scope(scope, _LINKABLE_SCOPES)
    notes += [n for n in (expiry_note, scope_note) if n]
    return render_page(
        "Create an API key",
        _form(session, name_value, expiry_value, scope_value, notes),
    )


@router.post("/create")
def create_submit(
    session: FormSession,
    name: Annotated[str, Form()] = "",
    expiration: Annotated[str, Form()] = "none",
    scope: Annotated[str, Form()] = _DEFAULT_SCOPE,
) -> Response:
    expiry_value, days, expiry_note = _parse_expiration(expiration)
    scope_value, scope_note = _parse_scope(scope, frozenset(SCOPE_TEMPLATES))
    notes = [n for n in (expiry_note, scope_note) if n]
    request: CreateAPIKeyRequest | None = None
    try:
        request = CreateAPIKeyRequest(
            name=name.strip(), expires_in_days=days, scope_template=scope_value
        )
    except ValidationError:
        notes.append(f"Enter a name of 1 to {MAX_KEY_NAME} characters.")
    if notes or request is None:
        return render_page(
            "Create an API key",
            _form(
                session, name.strip()[:MAX_KEY_NAME], expiry_value, scope_value, notes
            ),
            status_code=400,
        )
    with use_db_session() as db:
        raw_key, row = create_api_key(
            db,
            user_id=session.user_id,
            name=request.name,
            expires_in_days=request.expires_in_days,
            scopes=validate_scopes([scope_value]),
        )
        expires = aware_expires_at(row)
    return render_page(
        "API key created",
        "<h1>API key created</h1>"
        + nav(session)
        + f"<p><strong>{esc(request.name)}</strong> · {esc(scope_value)} · "
        f"expires {esc(expires.date().isoformat() if expires else 'never')}</p>"
        "<p class='warn'>Copy it now: it is shown only once. Don't refresh this "
        "page, which would create another key.</p>"
        f"<p><code style='font-size:1.05rem'>{esc(raw_key)}</code></p>"
        "<p>Send it as the <code>X-API-KEY</code> header.</p>"
        "<p><a href='/dashboard/settings/api-keys'>Back to API keys</a></p>",
    )


def _access(row: APIKey) -> str:
    """The template a key was made from, so the full-access one is obvious."""
    scopes = set(row.scopes or ["*"])
    for template in SCOPE_TEMPLATES:
        if set(validate_scopes([template])) == scopes:
            return template
    return ", ".join(sorted(scopes))


def _row(session: DashboardSession, row: APIKey, now: datetime) -> str:
    expires = aware_expires_at(row)
    if is_expired(row, now):
        state = "expired"
    elif expires is not None:
        state = f"expires {expires.date().isoformat()}"
    else:
        state = "no expiry"
    return (
        f"<li><strong>{esc(row.name)}</strong> <code>{esc(row.key_prefix)}…</code> · "
        f"{esc(_access(row))} · {esc(state)}"
        f"<form method='post' action='/dashboard/settings/api-keys/{row.id}/revoke' "
        "style='display:inline'>"
        + csrf_field(session)
        + "<button type='submit'>Revoke</button></form></li>"
    )


@router.get("")
def list_page(session: Session) -> Response:
    with use_db_session() as db:
        now = datetime.now(UTC)
        items = "".join(
            _row(session, r, now) for r in list_active_keys(db, session.user_id)
        )
    return render_page(
        "API keys",
        "<h1>API keys</h1>"
        + nav(session)
        + "<p><a href='/dashboard/settings/api-keys/create'>Create an API key</a></p>"
        + (f"<ul>{items}</ul>" if items else "<p>No keys yet.</p>"),
    )


@router.post("/{key_id}/revoke")
def revoke(session: FormSession, key_id: int) -> Response:
    with use_db_session() as db:
        revoked = revoke_api_key(db, key_id=key_id, user_id=session.user_id)
    if not revoked:
        return render_page("API keys", "<h1>Not found</h1>" + nav(session), 404)
    return RedirectResponse("/dashboard/settings/api-keys", status_code=303)
