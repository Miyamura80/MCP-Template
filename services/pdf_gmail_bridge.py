"""Gmail adapters for the PDF ports - the ONLY module importing both sides.

The PDF core (``pdf_forms_svc``, ``pdf_inspect``, ``pdf_render``,
``pdf_signing``, ``pdf_documents_repo``, ``models.pdf_forms``) knows nothing
about Gmail; this bridge registers the ``gmail_attachment`` source resolver
and the ``gmail_draft`` destination handler against the ports in
``services/pdf_ports.py`` at import time (``discover_services()`` imports
every ``services.*`` module, so registration happens before any transport
serves a call).

Extracting the PDF domain as a standalone add-on later = moving the core
modules out and replacing this one file. Enforced by the import-linter
contract in ``.importlinter``.
"""

from __future__ import annotations

import base64
from typing import Any

from models.gmail import GmailGetAttachmentInput
from services.gmail_messages_svc import gmail_get_attachment
from services.gmail_svc import _get_gmail_client
from services.pdf_ports import (
    ResolvedPdfSource,
    register_source_resolver,
)


class GmailAttachmentNotFoundError(Exception):
    """Raised when the attachment_id is not present on the given message."""

    def __init__(self, *, message_id: str, attachment_id: str) -> None:
        super().__init__(
            f"Attachment {attachment_id!r} not found on message {message_id!r}. "
            "Re-check the ids against gmail_get_thread."
        )


def _find_attachment_filename(
    svc: Any, message_id: str, attachment_id: str
) -> str | None:
    """Walk the message's MIME part tree for the attachment's filename."""
    msg = svc.users().messages().get(userId="me", id=message_id).execute()
    stack = [msg.get("payload") or {}]
    while stack:
        part = stack.pop()
        body = part.get("body") or {}
        if body.get("attachmentId") == attachment_id:
            return part.get("filename") or None
        stack.extend(part.get("parts") or [])
    return None


def _resolve_gmail_attachment(user_id: str, source: Any) -> ResolvedPdfSource:
    """Fetch a Gmail attachment's bytes + filename for a pdf_open session."""
    svc = _get_gmail_client(user_id)
    filename = _find_attachment_filename(svc, source.message_id, source.attachment_id)
    if filename is None:
        raise GmailAttachmentNotFoundError(
            message_id=source.message_id, attachment_id=source.attachment_id
        )
    # Reuse the existing service for the bytes: it normalizes Gmail's
    # base64url and enforces the attachment size ceiling.
    attachment = gmail_get_attachment(
        GmailGetAttachmentInput(
            user_id=user_id,
            message_id=source.message_id,
            attachment_id=source.attachment_id,
        )
    )
    return ResolvedPdfSource(
        filename=filename,
        data=base64.b64decode(attachment.data_base64),
    )


register_source_resolver("gmail_attachment", _resolve_gmail_attachment)
