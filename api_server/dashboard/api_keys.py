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

from fastapi import APIRouter, Form, Query
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
    DashboardForm,
    DashboardSession,
    DashboardUser,
)
from api_server.dashboard.ui import box, csrf_field, dashboard_page, esc
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
    status_code: int = 200,
) -> Response:
    warn = "".join(f"<p class='note'>{esc(n)}</p>" for n in notes)
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
    inner = (
        warn
        + "<form method='post' action='/dashboard/settings/api-keys/create'>"
        + csrf_field(session)
        + "<label class='field'><span>Name</span><input name='name' required "
        f"maxlength='{MAX_KEY_NAME}' value='{esc(name)}' "
        "placeholder='e.g. ci-bot'></label>"
        "<label class='field'><span>Expires</span>"
        f"<select name='expiration'>{expiry_opts}</select></label>"
        "<label class='field'><span>Access</span>"
        f"<select name='scope'>{scope_opts}</select></label>"
        "<div class='actions'><button class='primary' type='submit'>Create key</button>"
        "<a class='btn' href='/dashboard/settings/api-keys'>Cancel</a></div></form>"
    )
    return dashboard_page(
        session,
        active="api-keys",
        eyebrow="Credentials",
        title="Create an API key",
        lead="Check the details, then create. The key is shown once.",
        body=box("New key", inner),
        status_code=status_code,
        narrow=True,
    )


@router.get("/create")
def create_page(
    session: DashboardUser,
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
    return _form(session, name_value, expiry_value, scope_value, notes)


@router.post("/create")
def create_submit(
    session: DashboardForm,
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
        return _form(
            session,
            name.strip()[:MAX_KEY_NAME],
            expiry_value,
            scope_value,
            notes,
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
    inner = (
        "<div class='meta'>"
        f"<span class='pill on'>{esc(request.name)}</span>"
        f"<span class='pill'>{esc(scope_value)}</span>"
        f"<span class='pill'>expires {esc(expires.date().isoformat() if expires else 'never')}"
        "</span></div>"
        f"<code class='secret'>{esc(raw_key)}</code>"
        "<p class='warn'>Copy it now: it is shown only once. Don't refresh this "
        "page, which would create another key.</p>"
        "<p class='empty'>Send it as the <code>X-API-KEY</code> header.</p>"
        "<div class='actions'><a class='btn' href='/dashboard/settings/api-keys'>"
        "Back to API keys</a></div>"
    )
    return dashboard_page(
        session,
        active="api-keys",
        eyebrow="Credentials",
        title="API key created",
        body=box("Your key", inner),
        narrow=True,
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
        state = "<span class='pill bad'>expired</span>"
    elif expires is not None:
        state = f"<span class='pill'>{esc(expires.date().isoformat())}</span>"
    else:
        state = "<span class='pill'>never</span>"
    access = _access(row)
    tone = "bad" if access == "admin" else "on"
    return (
        f"<tr><td><strong>{esc(row.name)}</strong></td>"
        f"<td class='mono'>{esc(row.key_prefix)}…</td>"
        f"<td><span class='pill {tone}'>{esc(access)}</span></td><td>{state}</td>"
        f"<td><form method='post' action='/dashboard/settings/api-keys/{row.id}/revoke'>"
        + csrf_field(session)
        + "<button class='danger' type='submit'>Revoke</button></form></td></tr>"
    )


@router.get("")
def list_page(session: DashboardUser) -> Response:
    with use_db_session() as db:
        now = datetime.now(UTC)
        rows = "".join(
            _row(session, r, now) for r in list_active_keys(db, session.user_id)
        )
    table = (
        "<table><thead><tr><th>Name</th><th>Key</th><th>Access</th><th>Expires</th>"
        "<th></th></tr></thead><tbody>" + rows + "</tbody></table>"
        if rows
        else "<p class='empty'>No keys yet.</p>"
    )
    return dashboard_page(
        session,
        active="api-keys",
        eyebrow="Credentials",
        title="API keys",
        lead="Keys for the CLI, REST API and MCP clients that send "
        "<code>X-API-KEY</code>.",
        body="<div class='actions' style='margin:0 0 1.5rem'>"
        "<a class='btn primary' href='/dashboard/settings/api-keys/create'>"
        "Create an API key</a></div>" + box("Active keys", table),
    )


@router.post("/{key_id}/revoke")
def revoke(session: DashboardForm, key_id: int) -> Response:
    with use_db_session() as db:
        revoked = revoke_api_key(db, key_id=key_id, user_id=session.user_id)
    if not revoked:
        return dashboard_page(
            session, active="api-keys", eyebrow="Credentials", title="Not found",
            body="", status_code=404,
        )  # fmt: skip
    return RedirectResponse("/dashboard/settings/api-keys", status_code=303)
