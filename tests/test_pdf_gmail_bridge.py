"""Tests for the Gmail<->PDF bridge adapters (isolation seam)."""

import base64
from unittest.mock import MagicMock, patch

import pytest

from models.gmail import GmailAttachmentData
from models.pdf_forms import GmailAttachmentSource
from services import pdf_gmail_bridge as bridge
from services.pdf_ports import resolve_source
from tests.test_template import TestTemplate

_PDF = b"%PDF-1.7 bridge-test"


def _fake_gmail_client(payload: dict):
    svc = MagicMock()
    svc.users().messages().get(userId="me", id="m1").execute.return_value = {
        "payload": payload
    }
    return svc


def _nested_payload(attachment_id: str, filename: str) -> dict:
    return {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "body": {}},
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {
                        "filename": filename,
                        "mimeType": "application/pdf",
                        "body": {"attachmentId": attachment_id},
                    }
                ],
            },
        ],
    }


class TestGmailAttachmentResolver(TestTemplate):
    def test_resolves_filename_and_bytes(self):
        source = GmailAttachmentSource(message_id="m1", attachment_id="a1")
        att = GmailAttachmentData(
            message_id="m1",
            attachment_id="a1",
            size=len(_PDF),
            data_base64=base64.b64encode(_PDF).decode("ascii"),
        )
        with (
            patch.object(
                bridge,
                "_get_gmail_client",
                return_value=_fake_gmail_client(_nested_payload("a1", "nda.pdf")),
            ),
            patch.object(bridge, "gmail_get_attachment", return_value=att),
        ):
            # Dispatch through the port registry to prove registration works.
            resolved = resolve_source("u1", source)
        assert resolved.filename == "nda.pdf"
        assert resolved.data == _PDF

    def test_unknown_attachment_raises(self):
        source = GmailAttachmentSource(message_id="m1", attachment_id="missing")
        with (
            patch.object(
                bridge,
                "_get_gmail_client",
                return_value=_fake_gmail_client(_nested_payload("a1", "nda.pdf")),
            ),
            pytest.raises(bridge.GmailAttachmentNotFoundError),
        ):
            resolve_source("u1", source)
