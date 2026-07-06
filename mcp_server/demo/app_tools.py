"""App-only demo tools - called by the inbox/composer iframes, fixture-backed.

Mirrors ``mcp_server/app_tools/`` (same names, same shapes) so the committed
MCP App bundles work unchanged against the demo mount. Mutations are
simulated; drafts go through the bounded cache in :mod:`mcp_server.demo.state`
so the composer's save/refresh loop feels real.

Imported for its registration side effects at the bottom of
``mcp_server/demo/server.py`` (the same deferred-import pattern the main
server uses for its app_tools package).
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from mcp_server.demo import fixtures, state
from mcp_server.demo.server import demo_mcp
from models.gmail import (
    UNSET,
    GmailAttachmentData,
    GmailCurateInboxResult,
    GmailDiscardDraftResult,
    GmailDraft,
    GmailSendResult,
    GmailThread,
    _UnsetType,
)
from services.gmail_messages_svc import (
    GmailArchiveResult,
    GmailMarkDoneResult,
    GmailMarkReadResult,
    GmailUnmarkDoneResult,
)

_APP_ONLY_META = {"ui": {"visibility": ["app"]}}


# ---------------------------------------------------------------------------
# Inbox reader iframe
# ---------------------------------------------------------------------------


@demo_mcp.tool(
    name="gmail_inbox.refresh",
    description="Re-run gmail_curate_inbox (called by the inbox reader app).",
    meta=_APP_ONLY_META,
)
def inbox_refresh(
    user_id: str = "", query: str | None = None, limit: int = 10
) -> GmailCurateInboxResult:
    return fixtures.curated_inbox(limit)


@demo_mcp.tool(
    name="gmail_inbox.open_thread",
    description="Fetch a single thread for the inbox reader app.",
    meta=_APP_ONLY_META,
)
def inbox_open_thread(thread_id: str, user_id: str = "") -> GmailThread:
    return fixtures.get_thread(thread_id)


@demo_mcp.tool(
    name="gmail_inbox.mark_read",
    description="Mark a thread as read (simulated in the demo).",
    meta=_APP_ONLY_META,
)
def inbox_mark_read(thread_id: str, user_id: str = "") -> GmailMarkReadResult:
    fixtures.get_thread(thread_id)
    return GmailMarkReadResult(marked_read=True)


@demo_mcp.tool(
    name="gmail_inbox.archive",
    description="Archive a thread (simulated in the demo).",
    meta=_APP_ONLY_META,
)
def inbox_archive(thread_id: str, user_id: str = "") -> GmailArchiveResult:
    fixtures.get_thread(thread_id)
    return GmailArchiveResult(archived=True)


@demo_mcp.tool(
    name="gmail_inbox.reply",
    description="Create a reply draft on a thread (the composer app opens it next).",
    meta=_APP_ONLY_META,
)
def inbox_reply(
    thread_id: str,
    user_id: str = "",
    body: str | None = None,
    subject: str | None = None,
) -> GmailDraft:
    thread = fixtures.get_thread(thread_id)
    last = thread.messages[-1]
    orig_subject = subject or last.subject or ""
    return state.remember_draft(
        GmailDraft(
            draft_id=f"demo-d-{uuid4().hex[:8]}",
            to=last.from_,
            subject=(
                orig_subject
                if orig_subject.lower().startswith("re:")
                else f"Re: {orig_subject}"
            ),
            body=body or "",
            thread_id=thread_id,
        )
    )


@demo_mcp.tool(
    name="gmail_inbox.forward",
    description="Create a forward draft for a message in a thread (simulated).",
    meta=_APP_ONLY_META,
)
def inbox_forward(
    thread_id: str, subject: str = "", body: str = "", user_id: str = ""
) -> GmailDraft:
    fixtures.get_thread(thread_id)
    fwd_subject = subject if subject.lower().startswith("fwd:") else f"Fwd: {subject}"
    return state.remember_draft(
        GmailDraft(
            draft_id=f"demo-d-{uuid4().hex[:8]}",
            to="",
            subject=fwd_subject,
            body=body,
            thread_id=thread_id,
        )
    )


@demo_mcp.tool(
    name="gmail_inbox.mark_done",
    description="Mark a thread as done (simulated in the demo).",
    meta=_APP_ONLY_META,
)
def inbox_mark_done(thread_id: str, user_id: str = "") -> GmailMarkDoneResult:
    fixtures.get_thread(thread_id)
    return GmailMarkDoneResult(marked_done=True, label_id="demo-done")


@demo_mcp.tool(
    name="gmail_inbox.unmark_done",
    description="Remove the done marker from a thread (simulated in the demo).",
    meta=_APP_ONLY_META,
)
def inbox_unmark_done(thread_id: str, user_id: str = "") -> GmailUnmarkDoneResult:
    fixtures.get_thread(thread_id)
    return GmailUnmarkDoneResult(unmarked_done=True)


class _SetFocusResult(BaseModel):
    ok: bool = True


class _FocusedEmailResult(BaseModel):
    focused: bool
    thread_id: str | None = None
    subject: str | None = None
    from_: str | None = None
    message_count: int = 0
    messages: list[dict[str, Any]] | None = None


@demo_mcp.tool(
    name="gmail_inbox.set_focus",
    description="Store which thread the user is currently viewing (called by inbox UI).",
    meta=_APP_ONLY_META,
)
def inbox_set_focus(
    thread_id: str | None = None,
    subject: str | None = None,
    from_: str | None = None,
    message_count: int = 0,
    messages: list[dict[str, Any]] | None = None,
    user_id: str = "",
) -> _SetFocusResult:
    if thread_id is None:
        state.set_focused(None)
    else:
        state.set_focused(
            {
                "thread_id": thread_id,
                "subject": subject,
                "from": from_,
                "message_count": message_count,
                "messages": messages,
            }
        )
    return _SetFocusResult()


@demo_mcp.tool(
    name="gmail_get_focused_email",
    description=(
        "Return the email thread the user is currently viewing in the inbox UI. "
        "Call this when the user asks about 'this email' or the open thread."
    ),
)
def get_focused_email(user_id: str = "") -> _FocusedEmailResult:
    data = state.focused
    if not data:
        return _FocusedEmailResult(focused=False)
    return _FocusedEmailResult(
        focused=True,
        thread_id=data.get("thread_id"),
        subject=data.get("subject"),
        from_=data.get("from"),
        message_count=data.get("message_count", 0),
        messages=data.get("messages"),
    )


# ---------------------------------------------------------------------------
# Composer iframe
# ---------------------------------------------------------------------------


@demo_mcp.tool(
    name="gmail_composer.save_draft",
    description="Persist the current composer fields onto the demo draft.",
    meta=_APP_ONLY_META,
)
def composer_save_draft(
    draft_id: str,
    user_id: str = "",
    to: str | None | _UnsetType = UNSET,
    subject: str | None | _UnsetType = UNSET,
    body: str | None | _UnsetType = UNSET,
    cc: str | None | _UnsetType = UNSET,
    bcc: str | None | _UnsetType = UNSET,
    attachments: list[dict] | None | _UnsetType = UNSET,
) -> GmailDraft:
    current = state.get_draft(draft_id)
    return state.remember_draft(
        current.model_copy(
            update={
                "to": state.patch_field(current.to, to),
                "subject": state.patch_field(current.subject, subject),
                "body": state.patch_field(current.body, body),
                "cc": state.patch_field(current.cc, cc),
                "bcc": state.patch_field(current.bcc, bcc),
            }
        )
    )


@demo_mcp.tool(
    name="gmail_composer.send",
    description="Persist composer fields then 'send' the draft (simulated - nothing is delivered).",
    meta=_APP_ONLY_META,
)
def composer_send(
    draft_id: str,
    user_id: str = "",
    to: str | None | _UnsetType = UNSET,
    subject: str | None | _UnsetType = UNSET,
    body: str | None | _UnsetType = UNSET,
    cc: str | None | _UnsetType = UNSET,
    bcc: str | None | _UnsetType = UNSET,
    attachments: list[dict] | None | _UnsetType = UNSET,
) -> GmailSendResult:
    draft = state.get_draft(draft_id)
    state.drop_draft(draft_id)
    return GmailSendResult(
        message_id=f"demo-sent-{uuid4().hex[:8]}",
        thread_id=draft.thread_id,
        sent_at=datetime.now(UTC),
    )


@demo_mcp.tool(
    name="gmail_composer.discard",
    description="Delete the current demo draft.",
    meta=_APP_ONLY_META,
)
def composer_discard(draft_id: str, user_id: str = "") -> GmailDiscardDraftResult:
    state.drop_draft(draft_id)
    return GmailDiscardDraftResult(discarded=True)


@demo_mcp.tool(
    name="gmail_composer.refresh",
    description="Re-fetch the current demo draft (composer polls for agent edits).",
    meta=_APP_ONLY_META,
)
def composer_refresh(draft_id: str, user_id: str = "") -> GmailDraft:
    return state.get_draft(draft_id)


@demo_mcp.tool(
    name="gmail_composer.get_thread",
    description="Fetch the full thread for display in the composer's thread panel.",
    meta=_APP_ONLY_META,
)
def composer_get_thread(thread_id: str, user_id: str = "") -> GmailThread:
    return fixtures.get_thread(thread_id)


@demo_mcp.tool(
    name="gmail_composer.get_attachment",
    description="Fetch the raw base64 data for a demo attachment.",
    meta=_APP_ONLY_META,
)
def composer_get_attachment(
    message_id: str, attachment_id: str, user_id: str = ""
) -> GmailAttachmentData:
    if attachment_id != "att-termsheet":
        raise ValueError(
            f"Unknown demo attachment {attachment_id!r}. The demo mailbox has "
            "one attachment: att-termsheet on message m-1 (thread t-1001)."
        )
    return GmailAttachmentData(
        message_id=message_id,
        attachment_id=attachment_id,
        size=184320,
        data_base64=fixtures.DEMO_PDF_BASE64,
    )
