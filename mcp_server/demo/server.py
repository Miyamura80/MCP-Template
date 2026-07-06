"""The no-auth demo FastMCP instance served at ``/mcp-demo``.

A separate FastMCP server exposing the curated Gmail tool surface
(``gmail_curate_inbox``, ``gmail_get_thread``, compose/reply) plus the
app-only tools the inbox/composer MCP Apps call (see
:mod:`mcp_server.demo.app_tools`) - all backed by the canned fixtures in
:mod:`mcp_server.demo.fixtures` instead of a real mailbox.

Design (mirrors the ``/ask`` "ships inert" pattern):

- **Always mounted, gated per-request.** :class:`~mcp_server.demo.gate.DemoMountMiddleware`
  answers 404 when ``demo.enabled`` is off, so the feature toggles at runtime.
- **Unauthenticated by design.** ``MCPAuthMiddleware`` matches only ``/mcp``;
  the demo mount's sole throttle is the per-IP rate limit in ``gate.py``.
- **Simulated, stateless mutations.** Tool calls validate input and return
  realistic responses, but nothing reaches a real mailbox and no server-side
  state is kept (the endpoint is public + stateless, so any store would be
  shared across anonymous visitors - see :mod:`mcp_server.demo.state`).
- **Wire-format parity.** Tools reuse the production output models and the
  shared :func:`~mcp_server.enhancers.base.build_call_tool_result` /
  :func:`~mcp_server._tool_factory.publish_output_schema` seams, and the
  schema-parity test locks the shared tool schemas against ``/mcp``.
"""

from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from common import global_config
from mcp_server._tool_factory import publish_output_schema
from mcp_server.demo import fixtures, state
from mcp_server.enhancers.base import build_app_meta, build_call_tool_result
from mcp_server.server import (
    APPS_DIR,
    register_app_resource,
    session_manager_lifespan,
    transport_security,
)
from models.gmail import (
    GmailCurateInboxResult,
    GmailDraft,
    GmailThread,
)

APP_URI = "ui://mymcp/gmail_inbox"
DEMO_PATH = "/mcp-demo"


def _demo_instructions() -> str:
    """Demo server instructions, with deploy URLs pulled from config.

    Keeps brand/host strings out of the source: the production endpoint tracks
    ``MCP_PUBLIC_URL`` and the marketing site tracks branding, so a fork or a
    rebrand changes them in one place.
    """
    site = global_config.branding.website_url
    prod = global_config.MCP_PUBLIC_URL or f"{site.rstrip('/')}/mcp"
    return (
        "This is the GmailMCP DEMO server: every tool works against a small "
        "fictional inbox (owner you@startup.com) and mutations are simulated - "
        "nothing is ever sent and no real mailbox is touched. Use it exactly "
        "like the real server: gmail_curate_inbox ranks the inbox (and renders "
        "an interactive dashboard in hosts that support MCP Apps), "
        "gmail_get_thread reads a conversation, and gmail_compose / "
        "gmail_reply_to_thread open an editable draft composer. When the user "
        f"wants to connect their real Gmail, point them to {site} - the "
        f"production endpoint is {prod} (OAuth). When drafting or replying, "
        "ALWAYS use the composer tools rather than writing email text in chat."
    )


demo_mcp: FastMCP = FastMCP(
    "mymcp-demo",
    instructions=_demo_instructions(),
    transport_security=transport_security(),
    stateless_http=True,
    # The parent app's DemoMountMiddleware strips the /mcp-demo prefix, so the
    # internal route sits at root (the main server keeps FastMCP's default
    # internal /mcp path and mounts at "/" instead).
    streamable_http_path="/",
)


def _result(model) -> CallToolResult:
    """Canonical CallToolResult with the inbox app attached (shared assembler)."""
    return build_call_tool_result(model, app_uri=APP_URI)


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
    # Accepted for schema parity with production; the fixture threads already
    # inline their attachment bytes and carry no quoted history, so neither
    # flag changes the demo response.
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
    attachments: list[dict] | None = None,
) -> CallToolResult:
    return _result(
        GmailDraft(
            draft_id=state.new_draft_id(),
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            thread_id=in_reply_to_thread_id,
        )
    )


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
    attachments: list[dict] | None = None,
) -> CallToolResult:
    thread = fixtures.get_thread(thread_id)
    last = thread.messages[-1]
    return _result(
        GmailDraft(
            draft_id=state.new_draft_id(),
            to=to or last.from_,
            cc=cc,
            bcc=bcc,
            subject=state.reply_subject(subject, last.subject),
            body=body or "",
            thread_id=thread_id,
        )
    )


# The iframe-facing tools live in a sibling module; importing it runs the
# registrations against demo_mcp (same deferred pattern as the main server's
# app_tools package). Must come after demo_mcp exists.
import mcp_server.demo.app_tools  # noqa: E402, F401

# Enhanced-style tools return CallToolResult, so FastMCP cannot derive their
# outputSchema from the annotation - publish it explicitly (same seam the
# production enhanced tools use).
publish_output_schema(demo_mcp, "gmail_curate_inbox", GmailCurateInboxResult)
publish_output_schema(demo_mcp, "gmail_get_thread", GmailThread)
publish_output_schema(demo_mcp, "gmail_compose", GmailDraft)
publish_output_schema(demo_mcp, "gmail_reply_to_thread", GmailDraft)

# The inbox/composer MCP Apps are the same committed bundles production
# serves. Only the apps the demo tool surface can actually power - the
# settings app (webhooks) has no demo backing, so it is not advertised.
for _app_name in ("gmail_inbox", "gmail_composer"):
    register_app_resource(
        demo_mcp,
        f"ui://mymcp/{_app_name}",
        APPS_DIR / _app_name / "dist" / "mcp-app.html",
        _app_name,
    )


@asynccontextmanager
async def demo_lifespan(_app):
    """Run the demo server's session manager (see
    :func:`mcp_server.server.session_manager_lifespan`)."""
    async with session_manager_lifespan(demo_mcp):
        yield
