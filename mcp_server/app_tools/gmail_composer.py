"""App-only tools used by the gmail_composer MCP App.

These tools are callable by the iframe via ``mcpApp.callServerTool`` and are
hinted as ``visibility=["app"]`` so well-behaved hosts hide them from the
LLM. The wire payload carries ``user_id`` exactly like the headless services;
a later wiring step will inject it from the authenticated principal.
"""

from mcp_server.server import mcp
from models.gmail import (
    GmailDiscardDraftInput,
    GmailDiscardDraftResult,
    GmailDraft,
    GmailGetDraftInput,
    GmailSendInput,
    GmailSendResult,
    GmailUpdateDraftInput,
)
from services.gmail_drafts_svc import (
    gmail_discard_draft as _gmail_discard_draft,
)
from services.gmail_drafts_svc import (
    gmail_get_draft as _gmail_get_draft,
)
from services.gmail_drafts_svc import (
    gmail_send as _gmail_send,
)
from services.gmail_drafts_svc import (
    gmail_update_draft as _gmail_update_draft,
)

_APP_META = {"ui": {"visibility": ["app"]}}


@mcp.tool(
    name="gmail_composer.save_draft",
    description="Persist the current composer fields onto an existing Gmail draft.",
    meta=_APP_META,
)
def save_draft(
    user_id: str,
    draft_id: str,
    to: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
) -> GmailDraft:
    return _gmail_update_draft(
        GmailUpdateDraftInput(
            user_id=user_id,
            draft_id=draft_id,
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
        )
    )


@mcp.tool(
    name="gmail_composer.send",
    description="Persist composer fields then send the draft via Gmail.",
    meta=_APP_META,
)
def send(
    user_id: str,
    draft_id: str,
    to: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
) -> GmailSendResult:
    _gmail_update_draft(
        GmailUpdateDraftInput(
            user_id=user_id,
            draft_id=draft_id,
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
        )
    )
    return _gmail_send(GmailSendInput(user_id=user_id, draft_id=draft_id))


@mcp.tool(
    name="gmail_composer.discard",
    description="Delete the current draft.",
    meta=_APP_META,
)
def discard(user_id: str, draft_id: str) -> GmailDiscardDraftResult:
    return _gmail_discard_draft(
        GmailDiscardDraftInput(user_id=user_id, draft_id=draft_id)
    )


@mcp.tool(
    name="gmail_composer.refresh",
    description="Re-fetch the current draft (used by the composer to poll for agent edits).",
    meta=_APP_META,
)
def refresh(user_id: str, draft_id: str) -> GmailDraft:
    return _gmail_get_draft(GmailGetDraftInput(user_id=user_id, draft_id=draft_id))
