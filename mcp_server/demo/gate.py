"""The ``/mcp-demo`` mount: enabled-flag gate + per-IP rate limit (pure ASGI).

Kept separate from the tool definitions in ``server.py`` because it is the
mount's most security-sensitive code and has zero dependence on the tool
layer. Installed as the app's *outermost* middleware.
"""

import json
import time
from collections import OrderedDict, deque

from starlette.types import ASGIApp, Receive, Scope, Send

from common import global_config
from mcp_server.demo.server import DEMO_PATH, demo_mcp

_rate_hits: OrderedDict[str, deque[float]] = OrderedDict()


def reset_rate_limiter() -> None:
    """Clear the per-IP window - test isolation hook."""
    _rate_hits.clear()


class DemoMountMiddleware:
    """Serve ``/mcp-demo`` from the demo FastMCP; pass everything else through.

    Installed as the app's *outermost* pure-ASGI middleware rather than a
    Starlette ``Mount`` for three reasons:

    - ``Mount`` 307-redirects the exact path (``/mcp-demo`` ->
      ``/mcp-demo/``), which some MCP clients won't follow on POST; here both
      forms hit the handler directly via a path rewrite.
    - Outermost placement keeps demo traffic away from
      :class:`~starlette.middleware.base.BaseHTTPMiddleware` stacks
      (RateLimit/RequestId), which would buffer FastMCP's SSE streams.
    - The gate can answer 404 when ``demo.enabled`` is off, so the feature
      flips at runtime ("ships inert", like ``/ask``).

    Throttling is a per-IP sliding window in process memory: the demo is
    explicitly non-durable and single-replica, so shared storage would be
    over-engineering. The IP table is bounded so an address-rotating client
    can't grow it without limit.
    """

    _MAX_TRACKED_IPS = 10_000

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.demo_app: ASGIApp = demo_mcp.streamable_http_app()
        # Module-level store (not per-instance): the middleware is rebuilt on
        # every app startup, and tests reset the window via
        # reset_rate_limiter() without reaching into the middleware stack.
        self._hits = _rate_hits

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (
            path == DEMO_PATH or path.startswith(f"{DEMO_PATH}/")
        ):
            await self.app(scope, receive, send)
            return
        if not global_config.demo.enabled:
            await _send_json(
                send, 404, {"detail": "The demo MCP endpoint is not enabled."}
            )
            return
        if not self._allow(_client_ip(scope)):
            await _send_json(
                send,
                429,
                {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32000,
                        "message": "Demo rate limit exceeded; retry in a minute.",
                    },
                    "id": None,
                },
                extra_headers=[(b"retry-after", b"60")],
            )
            return
        # The demo FastMCP serves at its internal root; strip our prefix.
        inner = dict(scope)
        inner["path"] = path[len(DEMO_PATH) :] or "/"
        inner["root_path"] = scope.get("root_path", "") + DEMO_PATH
        await self.demo_app(inner, receive, send)

    def _allow(self, ip: str) -> bool:
        now = time.time()
        window = self._hits.get(ip)
        if window is None:
            window = deque()
            self._hits[ip] = window
        self._hits.move_to_end(ip)
        while window and now - window[0] > 60.0:
            window.popleft()
        if len(window) >= global_config.demo.rate_limit_per_minute:
            return False
        window.append(now)
        while len(self._hits) > self._MAX_TRACKED_IPS:
            self._hits.popitem(last=False)
        return True


def _client_ip(scope: Scope) -> str:
    """The real peer address from the ASGI ``client`` triple.

    Deliberately NOT derived from ``X-Forwarded-For``: that header is
    client-controlled, so trusting it lets an attacker mint a fresh rate-limit
    bucket per request (and churn the IP LRU) simply by rotating the value.
    The rate limit is this mount's only throttle, so it must key on an address
    the client can't spoof. Run the server behind a trusted proxy with
    ``--proxy-headers`` / ``FORWARDED_ALLOW_IPS`` so uvicorn resolves
    ``scope['client']`` to the real client before we ever see it.
    """
    client = scope.get("client")
    return client[0] if client else "unknown"


async def _send_json(
    send: Send,
    status: int,
    payload: dict,
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    body = json.dumps(payload).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                *(extra_headers or []),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
