"""Gmail inbox enhancer - attaches the curated-inbox MCP App when the client supports it."""

from mcp_server.enhancers import enhance
from mcp_server.enhancers.base import EnhancedTool
from models.gmail import GmailCurateInboxInput, GmailCurateInboxResult

APP_URI = "ui://mymcp/gmail_inbox"


@enhance("gmail_curate_inbox", fallback="headless")
async def gmail_curate_inbox_enhanced(
    tool: EnhancedTool[GmailCurateInboxInput, GmailCurateInboxResult],
) -> GmailCurateInboxResult:
    result = tool.call()
    if tool.can_show_app:
        tool.send_app(APP_URI)
    return result
