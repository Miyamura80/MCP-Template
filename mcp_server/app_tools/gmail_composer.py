"""App-only tools used by the gmail_composer MCP App.

These tools are callable by the iframe via ``mcpApp.callServerTool`` and are
hinted as ``visibility=["app"]`` so well-behaved hosts hide them from the
LLM. ``user_id`` arrives on the wire but is overridden by the authenticated
principal when one is bound; see ``mcp_server/app_tools/_auth_guard.py``.
"""

from mcp_server.app_tools._auth_guard import guard_user_id
from mcp_server.server import mcp
from models.gmail import (
    AttachmentInput,
    AttachmentReference,
    GmailDiscardDraftInput,
    GmailDiscardDraftResult,
    GmailDraft,
    GmailGetDraftInput,
    GmailGetThreadInput,
    GmailSendInput,
    GmailSendResult,
    GmailThread,
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
from services.gmail_messages_svc import (
    gmail_get_thread as _gmail_get_thread,
)
from services.gmail_svc import _get_gmail_client

_APP_META = {"ui": {"visibility": ["app"]}}


@mcp.tool(
    name="gmail_composer.save_draft",
    description="Persist the current composer fields onto an existing Gmail draft.",
    meta=_APP_META,
)
def save_draft(
    draft_id: str,
    user_id: str = "",
    to: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    attachments: list[dict] | None = None,
) -> GmailDraft:
    uid = guard_user_id(user_id)
    atts: list[AttachmentInput | AttachmentReference] | None = (
        [AttachmentInput(**a) for a in attachments] if attachments else None
    )
    return _gmail_update_draft(
        GmailUpdateDraftInput(
            user_id=uid,
            draft_id=draft_id,
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            attachments=atts,
        )
    )


@mcp.tool(
    name="gmail_composer.send",
    description="Persist composer fields then send the draft via Gmail.",
    meta=_APP_META,
)
def send(
    draft_id: str,
    user_id: str = "",
    to: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    attachments: list[dict] | None = None,
) -> GmailSendResult:
    uid = guard_user_id(user_id)
    atts: list[AttachmentInput | AttachmentReference] | None = (
        [AttachmentInput(**a) for a in attachments] if attachments else None
    )
    _gmail_update_draft(
        GmailUpdateDraftInput(
            user_id=uid,
            draft_id=draft_id,
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            attachments=atts,
        )
    )
    return _gmail_send(GmailSendInput(user_id=uid, draft_id=draft_id))


@mcp.tool(
    name="gmail_composer.discard",
    description="Delete the current draft.",
    meta=_APP_META,
)
def discard(draft_id: str, user_id: str = "") -> GmailDiscardDraftResult:
    uid = guard_user_id(user_id)
    return _gmail_discard_draft(GmailDiscardDraftInput(user_id=uid, draft_id=draft_id))


@mcp.tool(
    name="gmail_composer.refresh",
    description="Re-fetch the current draft (used by the composer to poll for agent edits).",
    meta=_APP_META,
)
def refresh(draft_id: str, user_id: str = "") -> GmailDraft:
    uid = guard_user_id(user_id)
    return _gmail_get_draft(GmailGetDraftInput(user_id=uid, draft_id=draft_id))


@mcp.tool(
    name="gmail_composer.get_thread",
    description="Fetch the full thread for display in the composer's thread panel.",
    meta=_APP_META,
)
def get_thread(thread_id: str, user_id: str = "") -> GmailThread:
    uid = guard_user_id(user_id)
    return _gmail_get_thread(GmailGetThreadInput(user_id=uid, thread_id=thread_id))


@mcp.tool(
    name="gmail_composer.get_attachment",
    description="Fetch the raw base64 data for an attachment on a message.",
    meta=_APP_META,
)
def get_attachment(message_id: str, attachment_id: str, user_id: str = "") -> dict:
    """Return ``{data_base64}`` for the given attachment."""
    uid = guard_user_id(user_id)
    svc = _get_gmail_client(uid)
    att = (
        svc.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )
    raw = att.get("data", "")
    data = raw.replace("-", "+").replace("_", "/")
    data += "=" * (-len(data) % 4)
    return {"data_base64": data}
