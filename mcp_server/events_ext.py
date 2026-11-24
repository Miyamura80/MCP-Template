"""MCP Events extension (``io.modelcontextprotocol/events``, SEP-3415, Beta).

    events/list         -> the event types (``services/events/catalog.py``)
    events/poll         -> events since a cursor, for the authenticated principal
    events/subscribe    -> create or refresh a webhook subscription
    events/unsubscribe  -> remove one
    events/stream       -> push: a long-lived request that streams events
                           (``mcp_server/events_stream.py``)

``listChanged`` is not advertised: the catalog only changes with a deploy.
Every method needs an authenticated principal (stdio has none: ``Forbidden``),
and events are always that principal's own.

Spec: https://github.com/modelcontextprotocol/ext-events
(``specification/beta/events/events.mdx``). Beta: verify wire shapes against
the current spec before changing them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal

from anyio.to_thread import run_sync
from mcp.server.context import ServerRequestContext
from mcp.server.extension import Extension, MethodBinding
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, PaginatedRequestParams, RequestParams
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from mcp_server.events_stream import (
    StreamParams,
    StreamStart,
    stream,
    stream_slot,
)
from services import InvalidArgument
from services.events.catalog import EVENT_TYPES
from services.events.store import UnknownEventError, poll, stream_start
from services.events.webhooks import CallbackEndpointError, subscribe, unsubscribe
from src.utils.current_user import current_user

EXTENSION_ID = "io.modelcontextprotocol/events"

# Spec error codes (general-purpose MCP codes).
NOT_FOUND = -32023
FORBIDDEN = -32024
CALLBACK_ENDPOINT_ERROR = -32027


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class PollParams(RequestParams):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    cursor: str | None = None
    max_age_ms: int | None = Field(default=None, ge=0)
    max_events: int | None = Field(default=None, ge=1)


class WebhookDelivery(_Camel):
    mode: Literal["webhook"]
    url: str
    secret: str


class SubscribeParams(RequestParams):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    delivery: WebhookDelivery
    cursor: str | None = None
    max_age_ms: int | None = Field(default=None, ge=0)
    ttl_ms: int | None = Field(default=None, ge=1)


class DeliveryTarget(_Camel):
    url: str


class UnsubscribeParams(RequestParams):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    delivery: DeliveryTarget


def _principal() -> str:
    user = current_user()
    if user is None:
        raise MCPError(FORBIDDEN, "Events need an authenticated principal.")
    return user.user_id


async def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run blocking service code off the loop, mapping its errors to spec codes."""
    try:
        return await run_sync(lambda: fn(*args, **kwargs))
    except UnknownEventError as exc:
        raise MCPError(
            NOT_FOUND, str(exc), {"kind": "event", "name": exc.name}
        ) from exc
    except CallbackEndpointError as exc:
        raise MCPError(
            CALLBACK_ENDPOINT_ERROR, str(exc), {"reason": exc.reason}
        ) from exc
    except InvalidArgument as exc:
        raise MCPError(INVALID_PARAMS, str(exc)) from exc


def _reverifier(
    ctx: ServerRequestContext[Any, Any], user_id: str
) -> Callable[[], Awaitable[bool]]:
    """Re-run the /mcp authentication on this request's credentials, so a
    revoked API key or token ends an open stream (spec: SHOULD re-verify).

    Without an HTTP request to re-check (in-process callers) the stream keeps
    the principal it started with; ``stream_max_seconds`` still bounds it.
    """
    scope = getattr(ctx.request, "scope", None)

    async def still_authorized() -> bool:
        if scope is None:
            return True
        # Circular import: api_server.server mounts this package; load at call.
        from api_server.middleware.mcp_auth import authenticate_scope  # noqa: PLC0415

        user = await run_sync(authenticate_scope, scope)
        return user is not None and user.user_id == user_id

    return still_authorized


class Events(Extension):
    """Serve first-party events by poll and webhook."""

    identifier = EXTENSION_ID

    def methods(self) -> Sequence[MethodBinding]:
        return [
            MethodBinding("events/list", PaginatedRequestParams, self._list),
            MethodBinding("events/poll", PollParams, self._poll),
            MethodBinding("events/subscribe", SubscribeParams, self._subscribe),
            MethodBinding("events/unsubscribe", UnsubscribeParams, self._unsubscribe),
            MethodBinding("events/stream", StreamParams, self._stream),
        ]

    async def _list(
        self, _ctx: ServerRequestContext[Any, Any], _params: PaginatedRequestParams
    ) -> dict[str, Any]:
        # A handful of types: one page, so no cursor is ever issued.
        return {
            "resultType": "complete",
            "events": [t.descriptor() for t in EVENT_TYPES.values()],
        }

    async def _poll(
        self, _ctx: ServerRequestContext[Any, Any], params: PollParams
    ) -> dict[str, Any]:
        return await _call(
            poll,
            _principal(),
            params.name,
            params.arguments,
            cursor=params.cursor,
            max_age_ms=params.max_age_ms,
            max_events=params.max_events,
        )

    async def _subscribe(
        self, _ctx: ServerRequestContext[Any, Any], params: SubscribeParams
    ) -> dict[str, Any]:
        return await _call(
            subscribe,
            _principal(),
            params.name,
            params.arguments,
            url=params.delivery.url,
            secret=params.delivery.secret,
            cursor=params.cursor,
            max_age_ms=params.max_age_ms,
            ttl_ms=params.ttl_ms,
        )

    async def _stream(
        self, ctx: ServerRequestContext[Any, Any], params: StreamParams
    ) -> dict[str, Any]:
        user_id = _principal()
        # Validation errors happen here, before the stream opens, so they are
        # plain JSON-RPC errors; the loop itself never raises for a stream
        # problem (it sends frames instead).
        kind, arguments, after, truncated = await _call(
            stream_start,
            user_id,
            params.name,
            params.arguments,
            params.cursor,
            params.max_age_ms,
        )
        start = StreamStart(kind, arguments, after, truncated)
        async with stream_slot(user_id):
            return await stream(
                ctx, user_id, start, params.heartbeat_ms, _reverifier(ctx, user_id)
            )

    async def _unsubscribe(
        self, _ctx: ServerRequestContext[Any, Any], params: UnsubscribeParams
    ) -> dict[str, Any]:
        await _call(
            unsubscribe,
            _principal(),
            params.name,
            params.arguments,
            params.delivery.url,
        )
        return {"resultType": "complete"}
