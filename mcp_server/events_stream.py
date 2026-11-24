"""``events/stream``: push delivery for MCP Events (SEP-3415).

One long-lived request per subscription. On Streamable HTTP the POST answers
with SSE and the server writes notifications onto that same response:

    notifications/events/active      {cursor, truncated, heartbeatMs}  once
    notifications/events/event       one occurrence, with its cursor
    notifications/events/heartbeat   {cursor}  when nothing else was sent
    ... then a final empty result when the server ends the stream

Every notification carries ``_meta["io.modelcontextprotocol/subscriptionId"]``
(the request id), so a client multiplexing streams can tell them apart. The
client ends a stream by disconnecting (HTTP) or ``notifications/cancelled``.

This is what lets a host like ChatGPT wake an agent thread on an event instead
of the agent polling. The stream checks the event log every
``events.stream_check_ms`` and closes itself after ``events.stream_max_seconds``
so one request never pins a replica forever; the client reconnects with the
last cursor it saw and misses nothing (cursors are per principal and ordered
by commit, see ``services/events/store.py``).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import anyio
from anyio.to_thread import run_sync
from mcp.server.context import ServerRequestContext
from mcp.types import RequestParams
from pydantic import Field

from common import global_config
from services.events.store import stream_next, stream_start

SUBSCRIPTION_ID_META = "io.modelcontextprotocol/subscriptionId"


class StreamParams(RequestParams):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    cursor: str | None = None
    max_age_ms: int | None = Field(default=None, ge=0)
    heartbeat_ms: int | None = Field(default=None, ge=1000)


async def _notify(
    ctx: ServerRequestContext[Any, Any], method: str, params: Mapping[str, Any]
) -> None:
    """Write one notification onto this request's own response stream.

    ``ServerSession.send_notification`` only takes the spec's typed
    notifications; these are extension methods, so they go through the
    request-scoped channel directly. Pinned by ``tests/test_events_stream.py``.
    """
    body = {**params, "_meta": {SUBSCRIPTION_ID_META: ctx.request_id}}
    await ctx.session._request_outbound.notify(method, body)  # noqa: SLF001


async def stream(
    ctx: ServerRequestContext[Any, Any], user_id: str, params: StreamParams
) -> dict[str, Any]:
    """Run one push subscription until the client leaves or the window ends."""
    cfg = global_config.events
    arguments, after, truncated = await run_sync(
        stream_start, user_id, params.name, params.arguments, params.cursor,
        params.max_age_ms,
    )  # fmt: skip
    heartbeat_s = (params.heartbeat_ms or cfg.stream_heartbeat_ms) / 1000
    await _notify(
        ctx,
        "notifications/events/active",
        {
            "cursor": str(after),
            "truncated": truncated,
            "heartbeatMs": int(heartbeat_s * 1000),
        },
    )
    deadline = time.monotonic() + cfg.stream_max_seconds
    last_sent = time.monotonic()
    while time.monotonic() < deadline:
        events, after = await run_sync(
            stream_next, user_id, params.name, arguments, after
        )
        for event in events:
            await _notify(ctx, "notifications/events/event", event)
        if events:
            last_sent = time.monotonic()
        elif time.monotonic() - last_sent >= heartbeat_s:
            await _notify(ctx, "notifications/events/heartbeat", {"cursor": str(after)})
            last_sent = time.monotonic()
        await anyio.sleep(cfg.stream_check_ms / 1000)
    # Window over: the client reconnects from the last cursor it received.
    return {"resultType": "complete"}
