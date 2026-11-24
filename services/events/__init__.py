"""MCP Events (``io.modelcontextprotocol/events``, SEP-3415): first-party events.

``emit`` is the one way to raise an event; the MCP extension
(``mcp_server/events_ext.py``) serves ``events/list``, ``events/poll``,
``events/subscribe`` and ``events/unsubscribe`` on top of ``store`` and
``webhooks``. Push (``events/stream``) is not offered: no client needs it yet,
and long-lived streams fight this server's stateless, horizontally scaled
deployment.
"""

from services.events.store import emit

__all__ = ["emit"]
