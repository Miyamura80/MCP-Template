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
from pydantic import BaseModel

from models.gmail import (
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
from services.gmail_svc import (
    _build_raw_message,
    _get_gmail_client,
    _headers_to_dict,
    _parse_message_resource,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _draft_resource_to_model(draft: dict[str, Any]) -> GmailDraft:
    """Map a Gmail ``drafts.get(format=full)`` payload to ``GmailDraft``."""
    msg = draft.get("message") or {}
    parsed = _parse_message_resource(msg)
    return GmailDraft(
        draft_id=draft.get("id") or "",
        thread_id=parsed.get("thread_id"),
        to=parsed.get("to"),
        cc=parsed.get("cc"),
        bcc=None,  # Gmail does not echo Bcc back to the sender
        subject=parsed.get("subject"),
        body=parsed.get("body_text"),
    )


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
    summaries: list[_DraftSummary] = []
    for stub in listing.get("drafts", []) or []:
        draft_id = stub.get("id")
        if not draft_id:
            continue
        meta = (
            svc.users()
            .drafts()
            .get(
                userId="me",
                id=draft_id,
                format="metadata",
                metadataHeaders=["To", "Subject"],
            )
            .execute()
        )
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
    description="Patch fields on an existing Gmail draft",
    input_model=GmailUpdateDraftInput,
    output_model=GmailDraft,
)
def gmail_update_draft(input: GmailUpdateDraftInput) -> GmailDraft:
    """Patch fields on a draft, preserving anything the caller left as ``None``."""
    svc = _get_gmail_client(input.user_id)
    current = (
        svc.users()
        .drafts()
        .get(userId="me", id=input.draft_id, format="full")
        .execute()
    )
    parsed = _parse_message_resource(current.get("message") or {})

    to = input.to if input.to is not None else (parsed.get("to") or "")
    subject = (
        input.subject if input.subject is not None else (parsed.get("subject") or "")
    )
    body = input.body if input.body is not None else (parsed.get("body_text") or "")
    cc = input.cc if input.cc is not None else parsed.get("cc")
    bcc = input.bcc  # Gmail never echoes Bcc; only update when explicitly set

    raw = _build_raw_message(to=to, subject=subject, body=body, cc=cc, bcc=bcc)

    body_dict: dict[str, Any] = {"message": {"raw": raw}}
    thread_id = parsed.get("thread_id")
    if thread_id:
        body_dict["message"]["threadId"] = thread_id

    updated = (
        svc.users()
        .drafts()
        .update(userId="me", id=input.draft_id, body=body_dict)
        .execute()
    )
    return _draft_resource_to_model(updated)


@service(
    name="gmail_compose",
    description="Create a new Gmail draft from the given fields",
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

    user_id: str
    thread_id: str
    body: str | None = None
    subject: str | None = None


@service(
    name="gmail_reply_to_thread",
    description="Create a reply draft on an existing Gmail thread",
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
