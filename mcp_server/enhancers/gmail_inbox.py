"""Gmail inbox enhancer - attaches the curated-inbox MCP App when the client supports it.

Both ``gmail_curate_inbox`` and ``gmail_get_thread`` route to the same inbox
app. The app's ``ontoolresult`` handler inspects the payload shape to decide
which view to render: thread list (curated inbox) or single thread reader.
"""

from mcp_server.enhancers import enhance
from mcp_server.enhancers.base import EnhancedTool
from models.gmail import (
    GmailCurateInboxInput,
    GmailCurateInboxResult,
    GmailGetThreadInput,
    GmailThread,
)

APP_URI = "ui://mymcp/gmail_inbox"


@enhance("gmail_curate_inbox", fallback="headless")
async def gmail_curate_inbox_enhanced(
    tool: EnhancedTool[GmailCurateInboxInput, GmailCurateInboxResult],
) -> GmailCurateInboxResult:
    result = tool.call()
    if tool.can_show_app:
        tool.send_app(APP_URI)
    return result


@enhance("gmail_get_thread", fallback="headless")
async def gmail_get_thread_enhanced(
    tool: EnhancedTool[GmailGetThreadInput, GmailThread],
) -> GmailThread:
    result = tool.call()
    if tool.can_show_app:
        tool.send_app(APP_URI)
    return result
