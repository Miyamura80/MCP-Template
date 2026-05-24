"""App-only tools used by the gmail_inbox MCP App.

These are visible to the iframe via ``visibility=["app"]`` but should not be
invoked by the LLM directly - the curated reader UI calls them on user action.
``user_id`` arrives on the wire but is overridden by the authenticated
principal when one is bound; see ``mcp_server/app_tools/_auth_guard.py``.
"""

from mcp_server.app_tools._auth_guard import guard_user_id
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
    GmailMarkDoneResult,
    GmailMarkReadResult,
    GmailThreadModifyInput,
    GmailUnmarkDoneResult,
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
    gmail_mark_thread_done as _gmail_mark_thread_done,
)
from services.gmail_messages_svc import (
    gmail_mark_thread_read as _gmail_mark_thread_read,
)
from services.gmail_messages_svc import (
    gmail_unmark_thread_done as _gmail_unmark_thread_done,
)

_APP_META = {"ui": {"visibility": ["app"]}}


@mcp.tool(
    name="gmail_inbox.refresh",
    description="Re-run gmail_curate_inbox (called by the inbox reader app).",
    meta=_APP_META,
)
def refresh(
    user_id: str = "",
    query: str | None = None,
    limit: int = 10,
) -> GmailCurateInboxResult:
    uid = guard_user_id(user_id)
    return _gmail_curate_inbox(
        GmailCurateInboxInput(user_id=uid, query=query, limit=limit)
    )


@mcp.tool(
    name="gmail_inbox.open_thread",
    description="Fetch a single thread for the inbox reader app.",
    meta=_APP_META,
)
def open_thread(thread_id: str, user_id: str = "") -> GmailThread:
    uid = guard_user_id(user_id)
    return _gmail_get_thread(GmailGetThreadInput(user_id=uid, thread_id=thread_id))


@mcp.tool(
    name="gmail_inbox.mark_read",
    description="Mark a thread as read (removes the UNREAD label).",
    meta=_APP_META,
)
def mark_read(thread_id: str, user_id: str = "") -> GmailMarkReadResult:
    uid = guard_user_id(user_id)
    return _gmail_mark_thread_read(
        GmailThreadModifyInput(user_id=uid, thread_id=thread_id)
    )


@mcp.tool(
    name="gmail_inbox.archive",
    description="Archive a thread (removes the INBOX label).",
    meta=_APP_META,
)
def archive(thread_id: str, user_id: str = "") -> GmailArchiveResult:
    uid = guard_user_id(user_id)
    return _gmail_archive_thread(
        GmailThreadModifyInput(user_id=uid, thread_id=thread_id)
    )


@mcp.tool(
    name="gmail_inbox.reply",
    description="Create a reply draft on a thread (the composer app opens it next).",
    meta=_APP_META,
)
def reply(
    thread_id: str,
    user_id: str = "",
    body: str | None = None,
    subject: str | None = None,
) -> GmailDraft:
    uid = guard_user_id(user_id)
    return _gmail_reply_to_thread(
        GmailReplyInput(user_id=uid, thread_id=thread_id, body=body, subject=subject)
    )


@mcp.tool(
    name="gmail_inbox.mark_done",
    description="Mark a thread as done (applies MCP/Done label, hides from curated inbox).",
    meta=_APP_META,
)
def mark_done(thread_id: str, user_id: str = "") -> GmailMarkDoneResult:
    uid = guard_user_id(user_id)
    return _gmail_mark_thread_done(
        GmailThreadModifyInput(user_id=uid, thread_id=thread_id)
    )


@mcp.tool(
    name="gmail_inbox.unmark_done",
    description="Remove the done marker from a thread (undo mark-done).",
    meta=_APP_META,
)
def unmark_done(thread_id: str, user_id: str = "") -> GmailUnmarkDoneResult:
    uid = guard_user_id(user_id)
    return _gmail_unmark_thread_done(
        GmailThreadModifyInput(user_id=uid, thread_id=thread_id)
    )
