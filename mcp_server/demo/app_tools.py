"""App-only demo tools - called by the inbox/composer iframes, fixture-backed.

Signatures (names, defaults, ``_meta``) match ``mcp_server/app_tools/`` exactly
so the committed MCP App bundles work unchanged and the schema-parity test
(``tests/test_mcp_demo.py::TestSchemaParity``) passes. Behavior differs by
design: mutations are simulated and stateless (see
:mod:`mcp_server.demo.state` for why no server-side state can exist here).

Imported for its registration side effects at the bottom of
``mcp_server/demo/server.py`` (the same deferred-import pattern the main
server uses for its app_tools package).
"""

from datetime import UTC, datetime
from typing import Any

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
    return GmailDraft(
        draft_id=state.new_draft_id(),
        to=last.from_,
        subject=state.reply_subject(subject, last.subject),
        body=body or "",
        thread_id=thread_id,
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
    return GmailDraft(
        draft_id=state.new_draft_id(),
        to="",
        subject=fwd_subject,
        body=body,
        thread_id=thread_id,
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
    # Deliberate no-op: persisting the caller-supplied `messages` payload on a
    # public, unauthenticated, stateless endpoint would make it a stored
    # prompt-injection channel between anonymous visitors (see
    # mcp_server/demo/state.py). The demo just acknowledges.
    return _SetFocusResult()


@demo_mcp.tool(
    name="gmail_get_focused_email",
    description=(
        "Return the email thread the user is currently viewing in the inbox UI. "
        "Call this when the user asks about 'this email' or the open thread."
    ),
)
def get_focused_email(user_id: str = "") -> _FocusedEmailResult:
    # No stored focus state in the demo (see set_focus); tell the model there
    # is nothing focused so it falls back to gmail_get_thread.
    return _FocusedEmailResult(focused=False)


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
    # `attachments` is accepted for schema parity with production; demo drafts
    # carry no attachment bytes, so it is not reflected back.
    attachments: list[dict] | None | _UnsetType = UNSET,
) -> GmailDraft:
    return state.echo_saved_draft(
        draft_id, to=to, subject=subject, body=body, cc=cc, bcc=bcc
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
    # Save-then-send mirrors production; both are simulated here. The saved
    # draft is reconstructed statelessly so `thread_id` flows to the result.
    draft = state.echo_saved_draft(draft_id, to=to, subject=subject, body=body)
    return GmailSendResult(
        message_id=f"demo-sent-{state.new_draft_id().split('-')[-1]}",
        thread_id=draft.thread_id,
        sent_at=datetime.now(UTC),
    )


@demo_mcp.tool(
    name="gmail_composer.discard",
    description="Delete the current demo draft.",
    meta=_APP_ONLY_META,
)
def composer_discard(draft_id: str, user_id: str = "") -> GmailDiscardDraftResult:
    return GmailDiscardDraftResult(discarded=True)


@demo_mcp.tool(
    name="gmail_composer.refresh",
    description="Re-fetch the current demo draft (composer polls for agent edits).",
    meta=_APP_ONLY_META,
)
def composer_refresh(draft_id: str, user_id: str = "") -> GmailDraft:
    # Stateless: return the seed draft for its known id, else an empty draft
    # carrying the id. The demo never has to surface another call's edits.
    return state.echo_saved_draft(draft_id)


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
