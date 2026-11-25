"""Absolute links to dashboard pages, for messages that leave the browser.

Kept free of the dashboard's routes so the billing layer can import it: a
quota error carries the billing link for the agent to hand its user.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from api_server.auth.authkit_auth import mcp_resource_url


def public_origin() -> str:
    """The server's public origin (the dashboard is served next to /mcp)."""
    parts = urlsplit(mcp_resource_url())
    return f"{parts.scheme}://{parts.netloc}"


def billing_url(query: str = "") -> str:
    """``/dashboard/billing``, absolute, with an optional ``?...`` query."""
    return f"{public_origin()}/dashboard/billing{query}"
