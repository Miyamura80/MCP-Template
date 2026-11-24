"""Small HTML helpers shared by the dashboard pages."""

from __future__ import annotations

import html

from api_server.dashboard.session import DashboardSession


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def nav(session: DashboardSession) -> str:
    return (
        "<p><a href='/dashboard/approvals'>Approvals</a> · "
        "<a href='/dashboard/approval'>Approval settings</a> · "
        "<a href='/dashboard/settings/api-keys'>API keys</a> · "
        f"signed in as <code>{esc(session.user_id)}</code></p>"
    )


def csrf_field(session: DashboardSession) -> str:
    return f"<input type='hidden' name='csrf' value='{esc(session.csrf)}'>"
