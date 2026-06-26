"""Gmail drafts + compose + send services.

All services here are headless: pure sync functions that take a Pydantic
input model and return a Pydantic output model. UI/enhancer affordances
(elicitation, MCP Apps, etc.) live in ``mcp_server/enhancers`` and never
touch this module.

``GmailNotConnectedError`` propagates from ``_get_gmail_client`` when the
user has no active token row; the FastMCP factory surfaces it as
``isError: true`` automatically.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger as log
from pydantic import BaseModel, Field

from models.gmail import (
    AttachmentInput,
    GmailComposeInput,
    GmailDiscardDraftInput,
    GmailDiscardDraftResult,
    GmailDraft,
    GmailGetDraftInput,
    GmailListDraftsInput,
    GmailListDraftsResult,
    GmailSendInput,
    GmailSendResult,
    GmailUpdateDraftInput,
)
from models.gmail import (
    GmailDraftSummary as _DraftSummary,
)
from services import service
from services.gmail_draft_helpers import (
    _draft_resource_to_model,
    _rebuild_draft,
    _resolve_update_attachments,
)
from services.gmail_svc import (
    _build_raw_message,
    _get_gmail_client,
    _headers_to_dict,
    _parse_message_resource,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _draft_summary_from_metadata(draft: dict[str, Any]) -> _DraftSummary:
    msg = draft.get("message") or {}
    headers = _headers_to_dict((msg.get("payload") or {}).get("headers"))
    updated_at: datetime | None = None
    internal_date = msg.get("internalDate")
    if internal_date is not None:
        try:
            updated_at = datetime.fromtimestamp(int(internal_date) / 1000.0, tz=UTC)
        except (TypeError, ValueError):
            updated_at = None
    return _DraftSummary(
        draft_id=draft.get("id") or "",
        to=headers.get("to"),
        subject=headers.get("subject"),
        snippet=msg.get("snippet"),
        updated_at=updated_at,
    )


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


@service(
    name="gmail_list_drafts",
    description="List the user's Gmail drafts",
    input_model=GmailListDraftsInput,
    output_model=GmailListDraftsResult,
)
def gmail_list_drafts(input: GmailListDraftsInput) -> GmailListDraftsResult:
    """Return up to ``input.limit`` drafts with To/Subject metadata."""
    svc = _get_gmail_client(input.user_id)
    listing = svc.users().drafts().list(userId="me", maxResults=input.limit).execute()
    draft_ids = [
        stub["id"] for stub in (listing.get("drafts", []) or []) if stub.get("id")
    ]
    if not draft_ids:
        return GmailListDraftsResult(drafts=[])

    fetched: dict[str, dict] = {}
    batch = svc.new_batch_http_request()
    for did in draft_ids:
        req = (
            svc.users()
            .drafts()
            .get(
                userId="me",
                id=did,
                format="metadata",
                metadataHeaders=["To", "Subject"],
            )
        )

        def _cb(
            request_id: str, response: Any, exception: Any, _did: str = did
        ) -> None:
            if exception is None:
                fetched[_did] = response

        batch.add(req, callback=_cb)
    batch.execute()

    summaries: list[_DraftSummary] = []
    for did in draft_ids:
        meta = fetched.get(did)
        if meta:
            summaries.append(_draft_summary_from_metadata(meta))
    return GmailListDraftsResult(drafts=summaries)


@service(
    name="gmail_get_draft",
    description="Fetch a single Gmail draft by id",
    input_model=GmailGetDraftInput,
    output_model=GmailDraft,
)
def gmail_get_draft(input: GmailGetDraftInput) -> GmailDraft:
    svc = _get_gmail_client(input.user_id)
    draft = (
        svc.users()
        .drafts()
        .get(userId="me", id=input.draft_id, format="full")
        .execute()
    )
    return _draft_resource_to_model(draft)


@service(
    name="gmail_update_draft",
    description=(
        "Patch fields on an existing Gmail draft and open an interactive "
        "composer UI. Non-destructive by default: any field you OMIT is left "
        "unchanged on the draft, and a field set to null is CLEARED - this "
        "holds for to, cc, bcc, subject, body, and attachments. Omit "
        "'attachments' to keep every existing file untouched (so you can edit "
        "the body repeatedly without re-uploading); pass null or [] to drop "
        "them all. 'attachments' may mix new uploads (filename + mime_type + "
        "data_base64) with references to existing files ({attachment_id}) taken "
        "from a prior response, letting you preserve specific files by id. To "
        "add or remove a single file without touching the body, prefer "
        "gmail_add_attachment / gmail_remove_attachment. The returned draft "
        "echoes the saved state (recipients, subject, body_preview, and the "
        "full attachment list with ids/filenames/sizes). ALWAYS call this tool "
        "to write or edit draft content - NEVER compose email text as plain "
        "chat text. Pass your composed text in the 'body' parameter. Keep your "
        "chat response to one brief sentence since the user can edit in the UI."
    ),
    input_model=GmailUpdateDraftInput,
    output_model=GmailDraft,
)
def gmail_update_draft(input: GmailUpdateDraftInput) -> GmailDraft:
    """Patch a draft non-destructively: omitted fields stay, null clears them.

    Distinguishes "omitted" from "explicit null" via ``model_fields_set`` so a
    caller can change just the body without disturbing recipients, subject, or
    attachments. Because Gmail's ``drafts().update`` replaces the entire MIME
    message, existing attachments are re-downloaded and re-attached unless the
    caller explicitly clears or overrides them.
    """
    svc = _get_gmail_client(input.user_id)
    current = (
        svc.users()
        .drafts()
        .get(userId="me", id=input.draft_id, format="full")
        .execute()
    )
    message = current.get("message") or {}
    parsed = _parse_message_resource(message)
    message_id = message.get("id") or parsed.get("message_id") or ""

    fields_set = input.model_fields_set
    to = (input.to if "to" in fields_set else parsed.get("to")) or ""
    subject = (input.subject if "subject" in fields_set else parsed.get("subject")) or ""
    body = (input.body if "body" in fields_set else parsed.get("body_text")) or ""
    cc = input.cc if "cc" in fields_set else parsed.get("cc")
    # Gmail never echoes Bcc back, so it cannot be preserved across a rebuild;
    # only honor an explicitly-supplied value.
    bcc = input.bcc if "bcc" in fields_set else None

    attachment_uploads = _resolve_update_attachments(svc, message_id, parsed, input)

    return _rebuild_draft(
        svc,
        draft_id=input.draft_id,
        parsed=parsed,
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
        attachment_uploads=attachment_uploads,
    )


@service(
    name="gmail_compose",
    description=(
        "Create a new Gmail draft from the given fields and open an interactive "
        "composer UI. Returns the draft's actual saved state - draft_id, "
        "thread_id, recipients, subject, a body_preview, and the attachment list "
        "(each with attachment_id, filename, mime_type, size_bytes) - so you can "
        "verify what was saved without a follow-up gmail_get_draft. To edit it "
        "afterward use gmail_update_draft, which preserves omitted fields and "
        "keeps attachments unless you clear them. ALWAYS use this tool instead "
        "of composing email text in chat - it creates a real Gmail draft where "
        "the user can review, edit, and send. When an interactive UI is rendered "
        "alongside the result, keep your text response brief since the user can "
        "edit in the UI."
    ),
    input_model=GmailComposeInput,
    output_model=GmailDraft,
)
def gmail_compose(input: GmailComposeInput) -> GmailDraft:
    svc = _get_gmail_client(input.user_id)
    raw = _build_raw_message(
        to=input.to,
        subject=input.subject,
        body=input.body,
        cc=input.cc,
        bcc=input.bcc,
        in_reply_to_thread_id=input.in_reply_to_thread_id,
        attachments=input.attachments or None,
    )
    body_dict: dict[str, Any] = {"message": {"raw": raw}}
    if input.in_reply_to_thread_id:
        body_dict["message"]["threadId"] = input.in_reply_to_thread_id

    created = svc.users().drafts().create(userId="me", body=body_dict).execute()
    log.debug("Created Gmail draft id={}", created.get("id"))
    return _draft_resource_to_model(created)


@service(
    name="gmail_send",
    description="Send a previously-composed Gmail draft",
    input_model=GmailSendInput,
    output_model=GmailSendResult,
)
def gmail_send(input: GmailSendInput) -> GmailSendResult:
    svc = _get_gmail_client(input.user_id)
    sent = svc.users().drafts().send(userId="me", body={"id": input.draft_id}).execute()
    return GmailSendResult(
        message_id=sent.get("id") or "",
        thread_id=sent.get("threadId"),
        sent_at=datetime.now(UTC),
    )


@service(
    name="gmail_discard_draft",
    description="Delete a Gmail draft by id",
    input_model=GmailDiscardDraftInput,
    output_model=GmailDiscardDraftResult,
)
def gmail_discard_draft(input: GmailDiscardDraftInput) -> GmailDiscardDraftResult:
    """Delete a draft. Gmail's ``drafts().delete`` returns no body on success."""
    svc = _get_gmail_client(input.user_id)
    svc.users().drafts().delete(userId="me", id=input.draft_id).execute()
    log.debug("Discarded Gmail draft id={}", input.draft_id)
    return GmailDiscardDraftResult(discarded=True)


# ---------------------------------------------------------------------------
# Reply helper (creates a draft for an existing thread)
# ---------------------------------------------------------------------------


class GmailReplyInput(BaseModel):
    """Input for ``gmail_reply_to_thread``: create a reply draft on a thread.

    ``body`` defaults to an empty placeholder so the composer UI can populate
    it on the next turn. ``subject`` defaults to ``Re: <orig>`` derived from
    the thread's last message.
    """

    user_id: str = ""
    thread_id: str
    body: str | None = None
    subject: str | None = None
    attachments: list[AttachmentInput] = Field(default_factory=list)


@service(
    name="gmail_reply_to_thread",
    description="Create a reply draft on an existing Gmail thread. ALWAYS use this tool instead of composing reply text in chat - it creates a real Gmail draft and opens an interactive composer UI where the user can review, edit, and send. Pass your drafted reply in the 'body' parameter. When an interactive UI is rendered alongside the result, keep your text response brief since the user can edit in the UI.",
    input_model=GmailReplyInput,
    output_model=GmailDraft,
)
def gmail_reply_to_thread(input: GmailReplyInput) -> GmailDraft:
    """Create a reply draft attached to the given thread.

    Derives ``To`` from the last message's ``Reply-To`` header when present,
    falling back to ``From`` (RFC 5322 5.2.2). Prefixes the subject with ``Re:``
    unless the originating subject already starts with ``Re:``. Propagates the
    parent's ``Message-ID`` as ``In-Reply-To`` and appends to ``References``
    so non-Gmail MUAs also thread the conversation; Gmail itself uses the
    ``threadId`` on the API wrapper.
    """
    svc = _get_gmail_client(input.user_id)
    thread = (
        svc.users()
        .threads()
        .get(userId="me", id=input.thread_id, format="metadata")
        .execute()
    )
    messages = thread.get("messages") or []
    if not messages:
        raise ValueError(f"Thread {input.thread_id!r} has no messages to reply to")
    last_msg = messages[-1]
    headers = _headers_to_dict((last_msg.get("payload") or {}).get("headers"))
    to = headers.get("reply-to") or headers.get("from") or ""
    orig_subject = headers.get("subject") or ""
    if input.subject is not None:
        subject = input.subject
    elif orig_subject.lower().startswith("re:"):
        subject = orig_subject
    else:
        subject = f"Re: {orig_subject}" if orig_subject else "Re:"
    body = input.body if input.body is not None else ""

    parent_message_id = headers.get("message-id")
    parent_references = headers.get("references")
    in_reply_to = parent_message_id
    if parent_message_id and parent_references:
        references = f"{parent_references} {parent_message_id}"
    else:
        references = parent_references or parent_message_id

    raw = _build_raw_message(
        to=to,
        subject=subject,
        body=body,
        in_reply_to_thread_id=input.thread_id,
        in_reply_to=in_reply_to,
        references=references,
        attachments=input.attachments or None,
    )
    created = (
        svc.users()
        .drafts()
        .create(
            userId="me",
            body={"message": {"raw": raw, "threadId": input.thread_id}},
        )
        .execute()
    )
    log.debug("Created Gmail reply draft id={}", created.get("id"))
    return _draft_resource_to_model(created)
