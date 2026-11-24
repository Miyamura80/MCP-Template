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

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from api_server.dashboard.session import (
    DashboardSession,
    csrf_ok,
    current_session,
    require_csrf,
    require_session,
)
from api_server.dashboard.ui import box, csrf_field, dashboard_page, esc
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

Session = Annotated[DashboardSession, Depends(require_session)]
FormSession = Annotated[DashboardSession, Depends(require_csrf)]

_MODE_HELP = {
    "automatic": "runs without asking",
    "agent": "your agent confirms with confirm_action",
    "elicitation": "you confirm in the app you are chatting in",
    "browser": "you approve on this dashboard",
}


def _dangerous_tools() -> list[str]:
    return sorted(dangerous_tool_names())


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@pages.get("")
def dashboard_home() -> Response:
    return RedirectResponse("/dashboard/approvals", status_code=303)


_STATUS_PILL = {
    "pending": "on",
    "approved": "on",
    "executed": "ok",
    "denied": "bad",
    "failed": "bad",
}


def _pill(text: str, tone: str = "") -> str:
    return f"<span class='pill {tone}'>{esc(text)}</span>"


@pages.get("/approvals")
def approvals_page(session: Session) -> Response:
    rows = list_approvals(session.user_id)
    table = (
        "<table><thead><tr><th>Tool</th><th>Status</th><th>Mode</th>"
        "<th>Requested</th></tr></thead><tbody>"
        + "".join(
            f"<tr><td><a href='/dashboard/approvals/{esc(v.id)}'><code>{esc(v.tool)}"
            f"</code></a></td><td>{_pill(v.status, _STATUS_PILL.get(v.status, ''))}</td>"
            f"<td>{_pill(v.mode)}</td>"
            f"<td class='mono'>{esc(v.created_at.strftime('%Y-%m-%d %H:%M'))}</td></tr>"
            for v in rows
        )
        + "</tbody></table>"
    )
    return dashboard_page(
        session,
        active="approvals",
        eyebrow="Human in the loop",
        title="Approvals",
        lead="Dangerous calls your agents asked to make. Approve one and the "
        "agent's next retry runs it.",
        body=box(
            "Held calls", table if rows else "<p class='empty'>Nothing waiting.</p>"
        ),
    )


@pages.get("/approvals/{approval_id}")
def approval_page(session: Session, approval_id: str) -> Response:
    try:
        view, args = get_review(session.user_id, approval_id)
    except InvalidArgument:
        return dashboard_page(
            session, active="approvals", eyebrow="Approval", title="Not found",
            body="", status_code=404,
        )  # fmt: skip
    meta = (
        "<div class='meta'>"
        + _pill(view.status, _STATUS_PILL.get(view.status, ""))
        + _pill(f"mode {view.mode}")
        + _pill(f"expires {view.expires_at.strftime('%Y-%m-%d %H:%M')}")
        + "</div>"
    )
    inner = meta + f"<pre>{esc(json.dumps(args, indent=2, ensure_ascii=False))}</pre>"
    if view.status == "pending":
        inner += (
            f"<form method='post' action='/dashboard/approvals/{esc(view.id)}' "
            "class='actions'>"
            + csrf_field(session)
            + "<button class='primary' name='decision' value='approve' type='submit'>"
            "Approve</button>"
            "<button name='decision' value='deny' type='submit'>Deny</button></form>"
        )
    elif view.status == "approved":
        inner += (
            "<p class='note'>Approved. It runs when your agent retries the call.</p>"
        )
    elif view.error:
        inner += f"<p class='warn'>{esc(view.error)}</p>"
    return dashboard_page(
        session,
        active="approvals",
        eyebrow="Approval",
        title=view.tool,
        lead="These are the exact arguments that will run. Nothing else can.",
        body=box("Held call", inner),
        narrow=True,
    )


@pages.post("/approvals/{approval_id}")
def decide_page(
    session: FormSession, approval_id: str, decision: Annotated[str, Form()]
) -> Response:
    try:
        _decide(session.user_id, approval_id, decision)
    except InvalidArgument as exc:
        return dashboard_page(
            session, active="approvals", eyebrow="Approval", title="Can't do that",
            body=f"<p class='warn'>{esc(exc)}</p>", status_code=400,
        )  # fmt: skip
    return RedirectResponse(f"/dashboard/approvals/{approval_id}", status_code=303)


def _select(name: str, current: str | None, allow_default: bool) -> str:
    opts = ["<option value=''>Use default</option>"] if allow_default else []
    opts += [
        f"<option value='{m}'{' selected' if m == current else ''}>"
        f"{m}: {_MODE_HELP[m]}</option>"
        for m in APPROVAL_MODES
    ]
    return f"<select name='{esc(name)}'>{''.join(opts)}</select>"


@pages.get("/approval")
def settings_page(session: Session) -> Response:
    settings = get_settings(session.user_id)
    overrides = "".join(
        f"<label class='field'><span>{esc(tool)}</span>"
        f"{_select('tool:' + tool, settings.overrides.get(tool), True)}</label>"
        for tool in _dangerous_tools()
    )
    form = (
        "<form method='post' action='/dashboard/approval'>"
        + csrf_field(session)
        + box(
            "Default",
            "<label class='field'><span>Every dangerous tool</span>"
            + _select("mode", settings.mode, False)
            + "</label>",
        )
        + box("Per tool", overrides or "<p class='empty'>No dangerous tools.</p>")
        + "<div class='actions'><button class='primary' type='submit'>Save</button></div>"
        "</form>"
    )
    return dashboard_page(
        session,
        active="approval",
        eyebrow="Settings",
        title="Approval settings",
        lead="How much checking dangerous tools get. An agent's MCP URL "
        "(<code>?approval=</code>) can make this stricter, never looser.",
        body=form,
        narrow=True,
    )


@pages.post("/approval")
async def save_settings_page(request: Request, session: Session) -> Response:
    form = await request.form()  # per-tool fields are dynamic, so read it whole
    if not csrf_ok(session, str(form.get("csrf", ""))):
        return dashboard_page(
            session, active="approval", eyebrow="Settings", title="Request expired",
            body="<p class='warn'>Reload and retry.</p>", status_code=403,
        )  # fmt: skip
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
        return dashboard_page(
            session, active="approval", eyebrow="Settings", title="Not saved",
            body=f"<p class='warn'>{esc(exc)}</p>", status_code=400,
        )  # fmt: skip
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
