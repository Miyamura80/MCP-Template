"""The no-auth demo FastMCP instance and its ``/mcp-demo`` mount.

A separate FastMCP server exposing the curated Gmail tool surface
(``gmail_curate_inbox``, ``gmail_get_thread``, compose/reply) plus the
app-only tools the inbox/composer MCP Apps call (see
:mod:`mcp_server.demo.app_tools`) - all backed by the canned fixtures in
:mod:`mcp_server.demo.fixtures` instead of a real mailbox.

Design (mirrors the ``/ask`` "ships inert" pattern):

- **Always mounted, gated per-request.** :class:`DemoMountMiddleware` answers
  404 when ``demo.enabled`` is off, so tests and deployments toggle the
  feature at runtime without re-building the app.
- **Unauthenticated by design.** ``MCPAuthMiddleware`` matches only ``/mcp``;
  the demo mount's sole throttle is the per-IP sliding window here.
- **Simulated, stateless mutations.** Tool calls validate input and return
  realistic success responses, but nothing reaches a real mailbox. The one
  concession to UX is a small bounded in-process draft cache
  (:mod:`mcp_server.demo.state`) so the composer app's save/refresh loop feels
  real; it is best-effort and evaporates on restart.
- **Wire-format parity.** Tools reuse the production output models and the
  same ``_meta.ui`` app wiring, so what a client sees on ``/mcp-demo`` is
  exactly what ``/mcp`` serves after OAuth.
"""

import json
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel
from starlette.types import ASGIApp, Receive, Scope, Send

from common import global_config
from mcp_server._tool_factory import _patch_output_schema
from mcp_server.demo import fixtures, state
from mcp_server.enhancers.base import build_app_meta
from mcp_server.server import _APPS_DIR, _register_app_resource, _transport_security
from models.gmail import (
    GmailCurateInboxResult,
    GmailDraft,
    GmailThread,
)

APP_URI = "ui://mymcp/gmail_inbox"
DEMO_PATH = "/mcp-demo"

_DEMO_INSTRUCTIONS = (
    "This is the GmailMCP DEMO server: every tool works against a small "
    "fictional inbox (owner you@startup.com) and mutations are simulated - "
    "nothing is ever sent and no real mailbox is touched. Use it exactly like "
    "the real server: gmail_curate_inbox ranks the inbox (and renders an "
    "interactive dashboard in hosts that support MCP Apps), gmail_get_thread "
    "reads a conversation, and gmail_compose / gmail_reply_to_thread open an "
    "editable draft composer. When the user wants to connect their real Gmail, "
    "point them to https://gmailmcp.com - the production endpoint is "
    "https://mcp.gmailmcp.com/mcp (OAuth). When drafting or replying, ALWAYS "
    "use the composer tools rather than writing email text in chat."
)

demo_mcp: FastMCP = FastMCP(
    "mymcp-demo",
    instructions=_DEMO_INSTRUCTIONS,
    transport_security=_transport_security(),
    stateless_http=True,
    # The parent app's DemoMountMiddleware strips the /mcp-demo prefix, so the
    # internal route sits at root (the main server keeps FastMCP's default
    # internal /mcp path and mounts at "/" instead).
    streamable_http_path="/",
)


def _result(model: BaseModel) -> CallToolResult:
    """Assemble the same CallToolResult shape the enhanced production tools emit."""
    return CallToolResult(
        content=[TextContent(type="text", text=model.model_dump_json())],
        structuredContent=model.model_dump(),
        _meta=build_app_meta(APP_URI),
    )


# ---------------------------------------------------------------------------
# LLM-facing tools (mirror the production surface; render the inbox app)
# ---------------------------------------------------------------------------


@demo_mcp.tool(
    name="gmail_curate_inbox",
    description=(
        "Rank recent inbox threads by importance (demo data - a small fictional "
        "inbox). When an interactive UI is rendered alongside the result, keep "
        "your text response brief since the user can browse details in the UI."
    ),
    meta=build_app_meta(APP_URI),
)
async def gmail_curate_inbox(
    user_id: str = "", query: str | None = None, limit: int = 10
) -> CallToolResult:
    return _result(fixtures.curated_inbox(limit))


@demo_mcp.tool(
    name="gmail_get_thread",
    description="Fetch a full conversation thread from the demo inbox.",
    meta=build_app_meta(APP_URI),
)
async def gmail_get_thread(
    thread_id: str,
    user_id: str = "",
    include_attachment_data: bool = False,
    strip_quoted_replies: bool = False,
) -> CallToolResult:
    return _result(fixtures.get_thread(thread_id))


@demo_mcp.tool(
    name="gmail_compose",
    description=(
        "Create a new email draft (demo - opens an editable composer UI; "
        "nothing is ever actually sent)."
    ),
    meta=build_app_meta(APP_URI),
)
async def gmail_compose(
    to: str,
    subject: str,
    body: str,
    user_id: str = "",
    cc: str | None = None,
    bcc: str | None = None,
    in_reply_to_thread_id: str | None = None,
) -> CallToolResult:
    draft = state.remember_draft(
        GmailDraft(
            draft_id=f"demo-d-{uuid4().hex[:8]}",
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            thread_id=in_reply_to_thread_id,
        )
    )
    return _result(draft)


@demo_mcp.tool(
    name="gmail_reply_to_thread",
    description=(
        "Create a reply draft on a demo thread (opens an editable composer UI; "
        "nothing is ever actually sent)."
    ),
    meta=build_app_meta(APP_URI),
)
async def gmail_reply_to_thread(
    thread_id: str,
    user_id: str = "",
    body: str | None = None,
    subject: str | None = None,
    to: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
) -> CallToolResult:
    thread = fixtures.get_thread(thread_id)
    last = thread.messages[-1]
    orig_subject = last.subject or ""
    reply_subject = subject or (
        orig_subject
        if orig_subject.lower().startswith("re:")
        else f"Re: {orig_subject}"
    )
    draft = state.remember_draft(
        GmailDraft(
            draft_id=f"demo-d-{uuid4().hex[:8]}",
            to=to or last.from_,
            cc=cc,
            bcc=bcc,
            subject=reply_subject,
            body=body or "",
            thread_id=thread_id,
        )
    )
    return _result(draft)


# The iframe-facing tools live in a sibling module; importing it runs the
# registrations against demo_mcp (same deferred pattern as the main server's
# app_tools package). Must come after demo_mcp exists.
import mcp_server.demo.app_tools  # noqa: E402, F401

# Enhanced-style tools return CallToolResult, so FastMCP cannot derive their
# outputSchema from the annotation - publish it explicitly (same mechanism as
# the production enhanced tools; see _tool_factory._patch_output_schema).
_patch_output_schema(demo_mcp, "gmail_curate_inbox", GmailCurateInboxResult)
_patch_output_schema(demo_mcp, "gmail_get_thread", GmailThread)
_patch_output_schema(demo_mcp, "gmail_compose", GmailDraft)
_patch_output_schema(demo_mcp, "gmail_reply_to_thread", GmailDraft)

# The inbox/composer MCP Apps are the same committed bundles production
# serves. Only the apps the demo tool surface can actually power - the
# settings app (webhooks) has no demo backing, so it is not advertised.
for _app_name in ("gmail_inbox", "gmail_composer"):
    _register_app_resource(
        demo_mcp,
        f"ui://mymcp/{_app_name}",
        _APPS_DIR / _app_name / "dist" / "mcp-app.html",
        _app_name,
    )


# ---------------------------------------------------------------------------
# Gate: enabled flag + per-IP rate limit (pure ASGI - never buffers SSE)
# ---------------------------------------------------------------------------

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
    """First X-Forwarded-For hop (the deploy sits behind a proxy) or peer address."""
    for key, value in scope.get("headers", []):
        if key == b"x-forwarded-for":
            first = value.decode("latin-1").split(",")[0].strip()
            if first:
                return first
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


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def demo_lifespan(_app):
    """Run the demo server's streamable-HTTP session manager.

    Always started (cheap - one task group) so a runtime enable of
    ``demo.enabled`` works without restarting the process.
    """
    sm = demo_mcp.session_manager
    # Same re-entry reset as the main server: StreamableHTTPSessionManager
    # refuses a second run() once started (tests with --count, hot-reload).
    sm._has_started = False
    async with sm.run():
        yield
