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

from models.gmail import (
    GmailComposeInput,
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
