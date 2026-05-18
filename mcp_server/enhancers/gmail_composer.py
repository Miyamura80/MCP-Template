"""Gmail composer enhancers - attach the composer iframe to compose/update results.

Both ``gmail_compose`` and ``gmail_update_draft`` reattach the same
``ui://mymcp/gmail_composer`` resource so an already-open composer iframe
re-renders with the agent's server-authoritative draft state. The headless
service result is returned unchanged so non-UI clients see identical output.
"""

from mcp_server.enhancers import enhance
from mcp_server.enhancers.base import EnhancedTool
from models.gmail import (
    GmailComposeInput,
    GmailDraft,
    GmailUpdateDraftInput,
)

APP_URI = "ui://mymcp/gmail_composer"


@enhance("gmail_compose", fallback="headless")
async def gmail_compose_enhanced(
    tool: EnhancedTool[GmailComposeInput, GmailDraft],
) -> GmailDraft:
    result = tool.call()
    if tool.can_show_app:
        tool.send_app(APP_URI)
    return result


@enhance("gmail_update_draft", fallback="headless")
async def gmail_update_draft_enhanced(
    tool: EnhancedTool[GmailUpdateDraftInput, GmailDraft],
) -> GmailDraft:
    result = tool.call()
    if tool.can_show_app:
        tool.send_app(APP_URI)
    return result
