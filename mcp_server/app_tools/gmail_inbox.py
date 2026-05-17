"""App-only tools used by the gmail_inbox MCP App.

These are visible to the iframe via ``visibility=["app"]`` but should not be
invoked by the LLM directly - the curated reader UI calls them on user action.
``user_id`` travels on the wire today; a later wiring step will inject it from
the authenticated principal.
"""

from mcp_server.server import mcp
from models.gmail import (
    GmailCurateInboxInput,
    GmailCurateInboxResult,
    GmailDraft,
    GmailGetThreadInput,
    GmailThread,
)
from services.gmail_drafts_svc import (
    GmailReplyInput,
)
from services.gmail_drafts_svc import (
    gmail_reply_to_thread as _gmail_reply_to_thread,
)
from services.gmail_messages_svc import (
    GmailArchiveResult,
    GmailMarkReadResult,
    GmailThreadModifyInput,
)
from services.gmail_messages_svc import (
    gmail_archive_thread as _gmail_archive_thread,
)
from services.gmail_messages_svc import (
    gmail_curate_inbox as _gmail_curate_inbox,
)
from services.gmail_messages_svc import (
    gmail_get_thread as _gmail_get_thread,
)
from services.gmail_messages_svc import (
    gmail_mark_thread_read as _gmail_mark_thread_read,
)

_APP_META = {"ui": {"visibility": ["app"]}}


@mcp.tool(
    name="gmail_inbox.refresh",
    description="Re-run gmail_curate_inbox (called by the inbox reader app).",
    meta=_APP_META,
)
def refresh(
    user_id: str,
    query: str | None = None,
    limit: int = 10,
) -> GmailCurateInboxResult:
    return _gmail_curate_inbox(
        GmailCurateInboxInput(user_id=user_id, query=query, limit=limit)
    )


@mcp.tool(
    name="gmail_inbox.open_thread",
    description="Fetch a single thread for the inbox reader app.",
    meta=_APP_META,
)
def open_thread(user_id: str, thread_id: str) -> GmailThread:
    return _gmail_get_thread(GmailGetThreadInput(user_id=user_id, thread_id=thread_id))


@mcp.tool(
    name="gmail_inbox.mark_read",
    description="Mark a thread as read (removes the UNREAD label).",
    meta=_APP_META,
)
def mark_read(user_id: str, thread_id: str) -> GmailMarkReadResult:
    return _gmail_mark_thread_read(
        GmailThreadModifyInput(user_id=user_id, thread_id=thread_id)
    )


@mcp.tool(
    name="gmail_inbox.archive",
    description="Archive a thread (removes the INBOX label).",
    meta=_APP_META,
)
def archive(user_id: str, thread_id: str) -> GmailArchiveResult:
    return _gmail_archive_thread(
        GmailThreadModifyInput(user_id=user_id, thread_id=thread_id)
    )


@mcp.tool(
    name="gmail_inbox.reply",
    description="Create a reply draft on a thread (the composer app opens it next).",
    meta=_APP_META,
)
def reply(
    user_id: str,
    thread_id: str,
    body: str | None = None,
    subject: str | None = None,
) -> GmailDraft:
    return _gmail_reply_to_thread(
        GmailReplyInput(
            user_id=user_id, thread_id=thread_id, body=body, subject=subject
        )
    )
