"""Bare server-rendered approval pages, plus their JSON twins.

    /dashboard/approvals        inbox: held dangerous calls, newest first
    /dashboard/approvals/{id}   the exact arguments; Approve / Deny
    /dashboard/approval         your mode, plus per-tool overrides

    GET  /api/v1/dashboard/session          {user_id, csrf_token}
    GET  /api/v1/approvals                  list
    POST /api/v1/approvals/{id}/approve     (X-CSRF-Token; the agent's retry runs it)
    POST /api/v1/approvals/{id}/deny        (X-CSRF-Token)
    GET  /api/v1/settings/approval
    PUT  /api/v1/settings/approval          (X-CSRF-Token)

All of it takes the dashboard session cookie only (``session.py``), never an
agent credential. The pages are deliberately plain: a template user replaces
them with their own app by pointing ``approvals.approve_url_template`` there
and calling the JSON routes.
"""

from __future__ import annotations

import html
import json
from typing import Annotated, Literal

from fastapi import APIRouter, Form, Header, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from api_server.dashboard.session import (
    DashboardSession,
    csrf_ok,
    current_session,
    login_redirect,
)
from api_server.oauth.flow import render_page
from models.approvals import (
    APPROVAL_MODES,
    ApprovalList,
    ApprovalSettings,
    ApprovalView,
)
from services import InvalidArgument, dangerous_tool_names
from services.approvals_svc import (
    approve_as_human,
    deny,
    get_review,
    get_settings,
    list_approvals,
    save_settings,
)

pages = APIRouter(prefix="/dashboard", tags=["dashboard"], include_in_schema=False)
api = APIRouter(prefix="/api/v1", tags=["approvals"])

_MODE_HELP = {
    "automatic": "runs without asking",
    "agent": "your agent confirms with confirm_action",
    "elicitation": "you confirm in the app you are chatting in",
    "browser": "you approve on this dashboard",
}


def _dangerous_tools() -> list[str]:
    return sorted(dangerous_tool_names())


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _nav(session: DashboardSession) -> str:
    return (
        "<p><a href='/dashboard/approvals'>Approvals</a> · "
        "<a href='/dashboard/approval'>Settings</a> · "
        f"signed in as <code>{_esc(session.user_id)}</code></p>"
    )


def _csrf_field(session: DashboardSession) -> str:
    return f"<input type='hidden' name='csrf' value='{_esc(session.csrf)}'>"


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@pages.get("")
def dashboard_home() -> Response:
    return RedirectResponse("/dashboard/approvals", status_code=303)


@pages.get("/approvals")
def approvals_page(request: Request) -> Response:
    session = current_session(request)
    if session is None:
        return login_redirect(request)
    rows = list_approvals(session.user_id)
    items = "".join(
        f"<li><a href='/dashboard/approvals/{_esc(v.id)}'><code>{_esc(v.tool)}</code></a> "
        f"· {_esc(v.status)} · {_esc(v.mode)} · "
        f"{_esc(v.created_at.isoformat(timespec='minutes'))}</li>"
        for v in rows
    )
    return render_page(
        "Approvals",
        "<h1>Approvals</h1>"
        + _nav(session)
        + (f"<ul>{items}</ul>" if items else "<p>Nothing waiting.</p>"),
    )


@pages.get("/approvals/{approval_id}")
def approval_page(request: Request, approval_id: str) -> Response:
    session = current_session(request)
    if session is None:
        return login_redirect(request)
    try:
        view, args = get_review(session.user_id, approval_id)
    except InvalidArgument:
        return render_page("Approval", "<h1>Not found</h1>" + _nav(session), 404)
    body = (
        f"<h1>{_esc(view.tool)}</h1>"
        + _nav(session)
        + f"<p>Status: <strong>{_esc(view.status)}</strong> · mode {_esc(view.mode)} · "
        f"expires {_esc(view.expires_at.isoformat(timespec='minutes'))}</p>"
        f"<pre>{_esc(json.dumps(args, indent=2, ensure_ascii=False))}</pre>"
    )
    if view.status == "pending":
        body += (
            f"<form method='post' action='/dashboard/approvals/{_esc(view.id)}'>"
            + _csrf_field(session)
            + "<button class='allow' name='decision' value='approve' type='submit'>"
            "Approve</button>"
            "<button name='decision' value='deny' type='submit'>Deny</button></form>"
        )
    elif view.status == "approved":
        body += "<p>Approved. It runs when your agent retries the call.</p>"
    elif view.error:
        body += f"<p class='warn'>{_esc(view.error)}</p>"
    return render_page(f"Approve {view.tool}", body)


@pages.post("/approvals/{approval_id}")
def decide_page(
    request: Request,
    approval_id: str,
    decision: Annotated[str, Form()],
    csrf: Annotated[str, Form()] = "",
) -> Response:
    session = current_session(request)
    if session is None:
        return login_redirect(request)
    if not csrf_ok(session, csrf):
        return render_page(
            "Approval", "<h1>Request expired</h1><p>Reload and retry.</p>", 403
        )
    try:
        _decide(session.user_id, approval_id, decision)
    except InvalidArgument as exc:
        return render_page("Approval", f"<h1>Can't do that</h1><p>{_esc(exc)}</p>", 400)
    return RedirectResponse(f"/dashboard/approvals/{approval_id}", status_code=303)


@pages.get("/approval")
def settings_page(request: Request) -> Response:
    session = current_session(request)
    if session is None:
        return login_redirect(request)
    settings = get_settings(session.user_id)

    def select(name: str, current: str | None, allow_default: bool) -> str:
        opts = ["<option value=''>(use default)</option>"] if allow_default else []
        opts += [
            f"<option value='{m}'{' selected' if m == current else ''}>"
            f"{m}: {_MODE_HELP[m]}</option>"
            for m in APPROVAL_MODES
        ]
        return f"<select name='{_esc(name)}'>{''.join(opts)}</select>"

    rows = "".join(
        f"<p><label><code>{_esc(tool)}</code> "
        f"{select('tool:' + tool, settings.overrides.get(tool), True)}</label></p>"
        for tool in _dangerous_tools()
    )
    return render_page(
        "Approval settings",
        "<h1>Approval settings</h1>"
        + _nav(session)
        + "<p>Applies to dangerous tools. An agent's MCP URL "
        "(<code>?approval=</code>) can make this stricter, never looser.</p>"
        "<form method='post' action='/dashboard/approval'>"
        + _csrf_field(session)
        + f"<p><label>Default {select('mode', settings.mode, False)}</label></p>"
        + rows
        + "<button class='allow' type='submit'>Save</button></form>",
    )


@pages.post("/approval")
async def save_settings_page(request: Request) -> Response:
    session = current_session(request)
    if session is None:
        return login_redirect(request)
    form = await request.form()
    if not csrf_ok(session, str(form.get("csrf", ""))):
        return render_page("Approval settings", "<h1>Request expired</h1>", 403)
    overrides = {
        str(key).removeprefix("tool:"): str(value)
        for key, value in form.multi_items()
        if str(key).startswith("tool:") and value
    }
    try:
        save_settings(
            session.user_id,
            ApprovalSettings.model_validate(
                {"mode": form.get("mode", "automatic"), "overrides": overrides}
            ),
        )
    except (InvalidArgument, ValueError) as exc:
        return render_page(
            "Approval settings", f"<h1>Not saved</h1><p>{_esc(exc)}</p>", 400
        )
    return RedirectResponse("/dashboard/approval", status_code=303)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def _require(request: Request, csrf: str | None = None, write: bool = False):
    session = current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Sign in at /dashboard/login.")
    if write and not csrf_ok(session, csrf):
        raise HTTPException(status_code=403, detail="Missing or wrong X-CSRF-Token.")
    return session


def _decide(user_id: str, approval_id: str, decision: str) -> ApprovalView:
    if decision == "approve":
        return approve_as_human(user_id, approval_id)
    if decision == "deny":
        return deny(user_id, approval_id)
    raise InvalidArgument("decision must be approve or deny")


@api.get("/dashboard/session")
def session_info(request: Request) -> dict[str, str]:
    session = _require(request)
    return {"user_id": session.user_id, "csrf_token": session.csrf}


@api.get("/approvals", response_model=ApprovalList)
def list_route(request: Request) -> ApprovalList:
    session = _require(request)
    return ApprovalList(approvals=list_approvals(session.user_id))


@api.post("/approvals/{approval_id}/{decision}", response_model=ApprovalView)
def decide_route(
    request: Request,
    approval_id: str,
    decision: Literal["approve", "deny"],
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> ApprovalView:
    session = _require(request, x_csrf_token, write=True)
    return _decide(session.user_id, approval_id, decision)


@api.get("/settings/approval", response_model=ApprovalSettings)
def get_settings_route(request: Request) -> ApprovalSettings:
    return get_settings(_require(request).user_id)


@api.put("/settings/approval", response_model=ApprovalSettings)
def put_settings_route(
    request: Request,
    body: ApprovalSettings,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> ApprovalSettings:
    session = _require(request, x_csrf_token, write=True)
    return save_settings(session.user_id, body)
