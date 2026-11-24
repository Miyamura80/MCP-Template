"""MCP Events (``io.modelcontextprotocol/events``, SEP-3415): first-party events.

``emit`` is the one way to raise an event; the MCP extension
(``mcp_server/events_ext.py``) serves ``events/list``, ``events/poll``,
``events/subscribe`` and ``events/unsubscribe`` on top of ``store`` and
``webhooks``. ``events/stream`` (push) lives in ``mcp_server/events_stream.py``: it is an
MCP request that stays open, so it belongs to the transport.
"""

from services.events.store import emit

__all__ = ["emit"]
