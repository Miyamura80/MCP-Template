"""The dashboard's page shell (brand bar + tabs) and small HTML helpers.

Styling lives in ``api_server/branded_page.py``; pages here only use its
classes (``box`` + ``tag`` panels, ``field``, ``primary``, ``danger``, ``pill``).
"""

from __future__ import annotations

import html

from fastapi.responses import HTMLResponse

from api_server.branded_page import render_page
from api_server.dashboard.session import DashboardSession
from common import global_config

TABS = (
    ("approvals", "/dashboard/approvals", "Approvals"),
    ("approval", "/dashboard/approval", "Approval settings"),
    ("api-keys", "/dashboard/settings/api-keys", "API keys"),
)


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def csrf_field(session: DashboardSession) -> str:
    return f"<input type='hidden' name='csrf' value='{esc(session.csrf)}'>"


def _topbar(session: DashboardSession, active: str) -> str:
    brand = esc(global_config.branding.title)
    tabs = "".join(
        f"<a href='{href}'{' aria-current=page' if key == active else ''}>{label}</a>"
        for key, href, label in TABS
    )
    return (
        "<header class='topbar'><div class='topbar-in'>"
        f"<a class='brand' href='/dashboard'><span class='mark'>{brand[:1]}</span>"
        f"{brand}</a><nav class='tabs' aria-label='Dashboard'>{tabs}</nav>"
        f"<span class='who'>{esc(session.user_id)}</span></div></header>"
    )


def dashboard_page(
    session: DashboardSession,
    *,
    active: str,
    title: str,
    eyebrow: str,
    body: str,
    lead: str = "",
    status_code: int = 200,
    narrow: bool = False,
) -> HTMLResponse:
    """A dashboard page: brand bar with tabs, an eyebrow label, a title, the body."""
    head = f"<span class='eyebrow'>{esc(eyebrow)}</span><h1>{esc(title)}</h1>" + (
        f"<p class='lead'>{lead}</p>" if lead else ""
    )
    return render_page(
        title,
        head + body,
        status_code,
        header=_topbar(session, active),
        narrow=narrow,
    )


def box(tag: str, inner: str) -> str:
    """A Hackbox panel: cyan wireframe with a filled tag label."""
    return f"<section class='box'><span class='tag'>{esc(tag)}</span>{inner}</section>"
