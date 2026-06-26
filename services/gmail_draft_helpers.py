"""Shared helpers for the Gmail draft services.

These functions are imported by both ``gmail_drafts_svc`` (compose / update /
get) and ``gmail_attachments_svc`` (add / remove attachment). They live here -
rather than in either service module - so neither has to import the other,
keeping the import graph acyclic.

The central idea: Gmail's ``drafts().update`` is a *whole-message replace*. To
edit one field (or one attachment) without clobbering the rest, callers read
the current draft, compute the desired full state, and hand it to
``_rebuild_draft``. Existing attachment bytes are re-downloaded from the live
message (Gmail stores them separately, keyed by ``attachmentId``) and
re-attached so they survive the replace.
"""

from __future__ import annotations

from typing import Any

from models.gmail import (
    AttachmentReference,
    GmailDraft,
    GmailDraftAttachment,
    GmailUpdateDraftInput,
)
from services.gmail_svc import _build_raw_message, _parse_message_resource


def _draft_resource_to_model(draft: dict[str, Any]) -> GmailDraft:
    """Map a Gmail ``drafts.get(format=full)`` payload to ``GmailDraft``."""
    msg = draft.get("message") or {}
    parsed = _parse_message_resource(msg)
    msg_id = parsed.get("message_id") or ""
    atts = [
        GmailDraftAttachment(
            filename=a.get("filename"),
            mime_type=a.get("mime_type"),
            size=a.get("size"),
            attachment_id=a.get("attachment_id"),
            message_id=msg_id,
        )
        for a in parsed.get("attachments") or []
        if a.get("filename")
    ]
    return GmailDraft(
        draft_id=draft.get("id") or "",
        thread_id=parsed.get("thread_id"),
        to=parsed.get("to"),
        cc=parsed.get("cc"),
        bcc=None,  # Gmail does not echo Bcc back to the sender
        subject=parsed.get("subject"),
        body=parsed.get("body_text"),
        attachments=atts,
    )


def _download_attachment_data(svc: Any, message_id: str, attachment_id: str) -> str:
    """Return the base64url-encoded bytes of an attachment already on a message.

    Gmail stores attachment bodies separately from the message envelope, keyed
    by ``attachmentId``. ``drafts().update`` replaces the whole MIME message, so
    to preserve an existing file across an edit we must re-download its bytes
    and re-attach them.
    """
    blob = (
        svc.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )
    return blob.get("data") or ""


def _current_attachments(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Named (non-inline) attachments currently on a parsed draft message."""
    return [a for a in (parsed.get("attachments") or []) if a.get("filename")]


def _existing_to_upload(
    svc: Any, message_id: str, meta: dict[str, Any]
) -> dict[str, str]:
    """Re-download an existing attachment into an upload dict for ``_build_raw_message``."""
    return {
        "filename": meta.get("filename") or "attachment",
        "mime_type": meta.get("mime_type") or "application/octet-stream",
        "data_base64": _download_attachment_data(
            svc, message_id, meta.get("attachment_id") or ""
        ),
    }


def _resolve_update_attachments(
    svc: Any,
    message_id: str,
    parsed: dict[str, Any],
    input: GmailUpdateDraftInput,
) -> list[dict[str, str]]:
    """Resolve the desired attachment uploads for an update, honoring omit/null.

    - ``attachments`` omitted (key absent)  -> preserve every existing file.
    - ``attachments`` is ``null`` or ``[]`` -> clear all files.
    - ``attachments`` is a list            -> each item is a new upload
      (``AttachmentInput``) or a reference to keep an existing file
      (``AttachmentReference``).
    """
    current = _current_attachments(parsed)
    if "attachments" not in input.model_fields_set:
        return [_existing_to_upload(svc, message_id, a) for a in current]
    if input.attachments is None:
        return []

    by_id = {a.get("attachment_id"): a for a in current}
    uploads: list[dict[str, str]] = []
    for item in input.attachments:
        if isinstance(item, AttachmentReference):
            meta = by_id.get(item.attachment_id)
            if meta is None:
                raise ValueError(
                    f"attachment_id {item.attachment_id!r} is not on draft "
                    f"{input.draft_id!r}"
                )
            uploads.append(_existing_to_upload(svc, message_id, meta))
        else:  # AttachmentInput - fresh upload
            uploads.append(
                {
                    "filename": item.filename,
                    "mime_type": item.mime_type,
                    "data_base64": item.data_base64,
                }
            )
    return uploads


def _rebuild_draft(
    svc: Any,
    *,
    draft_id: str,
    parsed: dict[str, Any],
    to: str,
    subject: str,
    body: str,
    cc: str | None,
    bcc: str | None,
    attachment_uploads: list[dict[str, str]],
) -> GmailDraft:
    """Replace a draft's MIME with the given state and return its echoed model.

    ``drafts().update`` is a whole-message replace; callers compute the desired
    field values (preserving what they did not change) and the full attachment
    set before calling this.
    """
    raw = _build_raw_message(
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
        attachments=attachment_uploads or None,
    )
    body_dict: dict[str, Any] = {"message": {"raw": raw}}
    thread_id = parsed.get("thread_id")
    if thread_id:
        body_dict["message"]["threadId"] = thread_id
    updated = (
        svc.users().drafts().update(userId="me", id=draft_id, body=body_dict).execute()
    )
    return _draft_resource_to_model(updated)
