"""``events/stream``: push delivery for MCP Events (SEP-3415).

One long-lived request per subscription. On Streamable HTTP the POST answers
with SSE and the server writes notifications onto that same response:

    notifications/events/active      {cursor, truncated, heartbeatMs}  once
    notifications/events/event       one occurrence, with its cursor
    notifications/events/heartbeat   {cursor}  when nothing else was sent
    notifications/events/terminated  {error}   the principal lost access
    ... then a final result {resultType, _meta} when the stream ends

Every frame, the final result included, carries
``_meta["io.modelcontextprotocol/subscriptionId"]`` (the request id), so a
client multiplexing streams can tell them apart. The client ends a stream by
disconnecting (HTTP) or ``notifications/cancelled``.

This lets a host wake an agent thread on an event instead of the agent
polling. The stream checks the event log every ``events.stream_check_ms``
(without waiting while a backlog drains), re-verifies the principal on every
heartbeat, and closes itself after ``events.stream_max_seconds`` so one
request never pins a replica forever; the client reconnects with the last
cursor it saw and misses nothing (cursors are per principal and ordered by
commit, see ``services/events/store.py``).

There is no graceful close on shutdown: a deploy drops every open stream at
once, and clients reconnect from their last cursor. The spec allows this.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, cast

import anyio
from anyio.to_thread import run_sync
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp.shared.subscriptions import SUBSCRIPTION_ID_META_KEY
from mcp.types import Notification, RequestParams, ServerNotification
from pydantic import Field

from common import global_config
from services.events.catalog import EventType
from services.events.store import stream_next

RESOURCE_EXHAUSTED = -32025
FORBIDDEN = -32024

FrameMethod = Literal[
    "notifications/events/active",
    "notifications/events/event",
    "notifications/events/heartbeat",
    "notifications/events/terminated",
]


class StreamParams(RequestParams):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    cursor: str | None = None
    max_age_ms: int | None = Field(default=None, ge=0)
    # A suggestion: clamped into the server's bounds, never rejected (spec).
    heartbeat_ms: int | None = None


class _Frame(Notification[dict[str, Any], FrameMethod]):
    """One extension notification. Not in the SDK's ``ServerNotification``
    union, but ``send_notification`` only serializes it, so a cast suffices."""


@dataclass(frozen=True)
class StreamStart:
    """A validated stream request (``services.events.store.stream_start``)."""

    kind: EventType
    arguments: dict[str, Any]
    after: int
    truncated: bool


# Stream polls get their own thread pool, so idle streams can never starve
# the default pool that sync tool calls run on. Created lazily: it binds to
# the running event loop.
_limiter: anyio.CapacityLimiter | None = None
_open: Counter[str] = Counter()


def _poll_limiter() -> anyio.CapacityLimiter:
    global _limiter
    if _limiter is None:
        _limiter = anyio.CapacityLimiter(16)
    return _limiter


def heartbeat_ms(requested: int | None) -> int:
    cfg = global_config.events
    wanted = requested or cfg.stream_heartbeat_ms
    return max(cfg.stream_heartbeat_min_ms, min(wanted, cfg.stream_heartbeat_max_ms))


@asynccontextmanager
async def stream_slot(user_id: str) -> AsyncIterator[None]:
    """Hold one of the user's open-stream slots (per replica) for the stream's
    life. Over the cap is refused before any frame is sent."""
    cap = global_config.events.max_streams_per_user
    if _open[user_id] >= cap:
        raise MCPError(
            RESOURCE_EXHAUSTED,
            f"At most {cap} open event streams per user; close one first.",
            {"limit": "streams", "max": cap},
        )
    _open[user_id] += 1
    try:
        yield
    finally:
        _open[user_id] -= 1
        if _open[user_id] <= 0:
            del _open[user_id]


class _Sender:
    def __init__(self, ctx: ServerRequestContext[Any, Any]) -> None:
        self._ctx = ctx
        self.meta = {SUBSCRIPTION_ID_META_KEY: ctx.request_id}

    async def send(self, method: FrameMethod, params: dict[str, Any]) -> None:
        """Write one frame onto this request's own response stream."""
        frame = _Frame(method=method, params={**params, "_meta": self.meta})
        await self._ctx.session.send_notification(
            cast(ServerNotification, frame), related_request_id=self._ctx.request_id
        )


async def stream(
    ctx: ServerRequestContext[Any, Any],
    user_id: str,
    start: StreamStart,
    heartbeat: int | None,
    still_authorized: Callable[[], Awaitable[bool]],
) -> dict[str, Any]:
    """Run one validated push subscription until it ends. Never raises for a
    stream problem: those are frames, because the stream is already open."""
    cfg = global_config.events
    out = _Sender(ctx)
    beat_ms = heartbeat_ms(heartbeat)
    after = start.after
    await out.send(
        "notifications/events/active",
        {"cursor": str(after), "truncated": start.truncated, "heartbeatMs": beat_ms},
    )
    deadline = time.monotonic() + cfg.stream_max_seconds
    last_sent = time.monotonic()
    while time.monotonic() < deadline:
        events, after, caught_up = await run_sync(
            stream_next, user_id, start.kind, start.arguments, after,
            limiter=_poll_limiter(),
        )  # fmt: skip
        for event in events:
            await out.send("notifications/events/event", event)
        if events:
            last_sent = time.monotonic()
        elif (time.monotonic() - last_sent) * 1000 >= beat_ms:
            if not await still_authorized():
                await out.send(
                    "notifications/events/terminated",
                    {
                        "error": {
                            "code": FORBIDDEN,
                            "message": "Forbidden",
                            "data": {"reason": "Access revoked"},
                        }
                    },
                )
                break
            await out.send("notifications/events/heartbeat", {"cursor": str(after)})
            last_sent = time.monotonic()
        if caught_up:  # a backlog drains without waiting between batches
            await anyio.sleep(cfg.stream_check_ms / 1000)
    # Window over (or terminated): the client reconnects from its last cursor.
    return {"resultType": "complete", "_meta": out.meta}
