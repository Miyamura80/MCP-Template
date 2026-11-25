"""Tests for the ceremony surfaces: app-only tools (US-007), the elicitation
fallback for no-app hosts (US-008), and pdf_export (US-009)."""

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.server.elicitation import AcceptedElicitation, DeclinedElicitation
from mcp.types import (
    ClientCapabilities,
    ElicitationCapability,
    ElicitResult,
    InputRequiredResult,
)
from mcp.types.version import MODERN_PROTOCOL_VERSIONS

from mcp_server.app_tools import pdf_signer as app_tools
from mcp_server.enhancers.base import EnhancedTool
from mcp_server.enhancers.pdf_forms import (
    _ElicitedSignature,
    pdf_request_signature_enhanced,
)
from models.pdf_forms import (
    GmailDraftDestination,
    PdfDelivery,
    PdfExportedAttachment,
    PdfExportInput,
    PdfRequestSignatureInput,
    SignaturePlacement,
)
from services import pdf_documents_repo as repo
from services import pdf_forms_svc, pdf_signing
from services.pdf_forms_svc import (
    PdfExportStateError,
    pdf_export,
    pdf_request_signature,
)
from tests.pdf_fixtures import make_acroform_pdf
from tests.pdf_harness import PdfSigningTestBase


def _mock_ctx(*, can_elicit: bool, elicit_result=None) -> MagicMock:
    ctx = MagicMock()
    ctx.session.check_client_capability = MagicMock(return_value=can_elicit)
    ctx.elicit = AsyncMock(return_value=elicit_result)
    return ctx


class TestPdfSignerAppTools(PdfSigningTestBase):
    def test_get_document_returns_bytes_for_iframe(self):
        doc_id = self._open_awaiting()
        result = app_tools.get_document(doc_id, user_id="u1")
        assert result.doc_id == doc_id
        assert result.status == "awaiting_signature"
        # Field placement resolved server-side to the exact stamp footprint
        # (the fixture's signature field rect is [100, 120, 300, 160]; the
        # anchor is inset +2/+4 and the rect derives from stamp geometry).
        assert result.stamp_page == 1
        assert result.stamp_rect is not None
        x0, y0, x1, y1 = result.stamp_rect
        assert x0 == pytest.approx(102.0)
        assert x1 > x0
        assert y0 < 124.0 < y1
        assert base64.b64decode(result.data_base64).startswith(b"%PDF-")

    def test_sign_with_host_confirmation_accepted(self):
        doc_id = self._open_awaiting()
        ctx = _mock_ctx(
            can_elicit=True,
            elicit_result=AcceptedElicitation(
                data=app_tools._SignConfirmation(confirm=True)
            ),
        )
        result = asyncio.run(
            app_tools.sign(
                ctx,
                doc_id=doc_id,
                typed_name="Eito Miyamura",
                consent=True,
                user_id="u1",
            )
        )
        assert result.status == "signed"
        assert result.signed_by == "Eito Miyamura"
        doc = repo.load_document(doc_id, "u1")
        assert doc.status == repo.PDF_STATUS_SIGNED
        assert (doc.audit or [])[-1]["confirmed_via_elicitation"] is True
        # The elicitation message names the document and the typed name.
        message = ctx.elicit.await_args.kwargs["message"]
        assert "nda.pdf" in message
        assert "Eito Miyamura" in message

    def test_sign_declined_confirmation_stays_awaiting(self):
        doc_id = self._open_awaiting()
        ctx = _mock_ctx(can_elicit=True, elicit_result=DeclinedElicitation())
        result = asyncio.run(
            app_tools.sign(
                ctx,
                doc_id=doc_id,
                typed_name="Eito Miyamura",
                consent=True,
                user_id="u1",
            )
        )
        assert result.status == "declined"
        doc = repo.load_document(doc_id, "u1")
        assert doc.status == repo.PDF_STATUS_AWAITING_SIGNATURE
        assert (doc.audit or [])[-1]["event"] == "sign_aborted"

    def test_sign_without_elicitation_capability_still_signs(self):
        # CLI/stdio hosts without elicitation: the typed name + consent from
        # the app UI are the ceremony; confirmed_via_elicitation records False.
        doc_id = self._open_awaiting()
        ctx = _mock_ctx(can_elicit=False)
        result = asyncio.run(
            app_tools.sign(
                ctx,
                doc_id=doc_id,
                typed_name="Eito Miyamura",
                consent=True,
                user_id="u1",
            )
        )
        assert result.status == "signed"
        doc = repo.load_document(doc_id, "u1")
        assert (doc.audit or [])[-1]["confirmed_via_elicitation"] is False

    def test_sign_requires_consent_and_name(self):
        doc_id = self._open_awaiting()
        ctx = _mock_ctx(can_elicit=False)
        with pytest.raises(pdf_signing.PdfSigningInputError):
            asyncio.run(
                app_tools.sign(
                    ctx,
                    doc_id=doc_id,
                    typed_name="Eito",
                    consent=False,
                    user_id="u1",
                )
            )
        with pytest.raises(pdf_signing.PdfSigningInputError):
            asyncio.run(
                app_tools.sign(
                    ctx, doc_id=doc_id, typed_name="  ", consent=True, user_id="u1"
                )
            )

    def test_sign_wrong_state_rejected(self):
        opened = self._open(make_acroform_pdf())
        ctx = _mock_ctx(can_elicit=False)
        with pytest.raises(pdf_signing.PdfSigningStateError):
            asyncio.run(
                app_tools.sign(
                    ctx,
                    doc_id=opened.doc_id,
                    typed_name="Eito",
                    consent=True,
                    user_id="u1",
                )
            )

    def test_cancel_returns_document_to_open(self):
        doc_id = self._open_awaiting()
        result = app_tools.cancel(doc_id, user_id="u1")
        assert result.status == "open"
        doc = repo.load_document(doc_id, "u1")
        assert doc.status == repo.PDF_STATUS_OPEN
        assert (doc.audit or [])[-1]["reason"] == "user_cancelled_in_app"


class TestElicitationFallback(PdfSigningTestBase):
    """US-008: hosts without iframe apps sign via a host-native dialog."""

    def _run_enhancer(self, doc_id: str, ctx):
        tool = EnhancedTool(
            ctx=ctx,
            input=PdfRequestSignatureInput(
                user_id="u1",
                doc_id=doc_id,
                placement=SignaturePlacement(field_name="signature"),
            ),
            service_fn=pdf_request_signature,
        )
        return asyncio.run(pdf_request_signature_enhanced(tool)), tool

    def test_app_host_gets_signer_app(self, monkeypatch):
        monkeypatch.delenv("MCP_DISABLE_APPS", raising=False)
        opened = self._open(make_acroform_pdf())
        result, tool = self._run_enhancer(opened.doc_id, _mock_ctx(can_elicit=True))
        assert result.status == "awaiting_user_signature"
        assert tool.app_resource_uri == "ui://mymcp/pdf_signer"

    def test_no_app_host_signs_via_elicitation(self, monkeypatch):
        monkeypatch.setenv("MCP_DISABLE_APPS", "1")
        opened = self._open(make_acroform_pdf())
        ctx = _mock_ctx(
            can_elicit=True,
            elicit_result=AcceptedElicitation(
                data=_ElicitedSignature(full_name="Eito Miyamura", consent=True)
            ),
        )
        result, _ = self._run_enhancer(opened.doc_id, ctx)
        assert result.status == "signed"
        doc = repo.load_document(opened.doc_id, "u1")
        assert doc.status == repo.PDF_STATUS_SIGNED
        last = (doc.audit or [])[-1]
        assert last["channel"] == "elicitation"
        assert last["typed_name"] == "Eito Miyamura"

    def test_declined_elicitation_reopens_document(self, monkeypatch):
        monkeypatch.setenv("MCP_DISABLE_APPS", "1")
        opened = self._open(make_acroform_pdf())
        ctx = _mock_ctx(can_elicit=True, elicit_result=DeclinedElicitation())
        result, _ = self._run_enhancer(opened.doc_id, ctx)
        assert result.status == "signing_declined"
        assert repo.load_document(opened.doc_id, "u1").status == repo.PDF_STATUS_OPEN

    def test_consent_withheld_counts_as_decline(self, monkeypatch):
        monkeypatch.setenv("MCP_DISABLE_APPS", "1")
        opened = self._open(make_acroform_pdf())
        ctx = _mock_ctx(
            can_elicit=True,
            elicit_result=AcceptedElicitation(
                data=_ElicitedSignature(full_name="Eito Miyamura", consent=False)
            ),
        )
        result, _ = self._run_enhancer(opened.doc_id, ctx)
        assert result.status == "signing_declined"
        assert repo.load_document(opened.doc_id, "u1").status == repo.PDF_STATUS_OPEN

    def test_no_channel_reports_unavailable(self, monkeypatch):
        monkeypatch.setenv("MCP_DISABLE_APPS", "1")
        opened = self._open(make_acroform_pdf())
        result, _ = self._run_enhancer(opened.doc_id, _mock_ctx(can_elicit=False))
        assert result.status == "signing_unavailable"
        assert "pdf_export" in result.guidance
        # Filling still works: the document was rolled back to open.
        assert repo.load_document(opened.doc_id, "u1").status == repo.PDF_STATUS_OPEN


# ElicitResult.content: a flat map of primitive answers.
_Answer = dict[str, str | int | float | list[str] | None]


def _modern_ctx(*, request_state=None, answer=None) -> MagicMock:
    """A 2026-07-28 request from a form-elicitation host (no back-channel)."""
    ctx = MagicMock()
    ctx.protocol_version = sorted(MODERN_PROTOCOL_VERSIONS)[-1]
    ctx.client_capabilities = ClientCapabilities(elicitation=ElicitationCapability())
    ctx.session.can_send_request = False
    ctx.request_state = request_state
    ctx.input_responses = {"signature": answer} if answer is not None else None
    return ctx


class TestMultiRoundTripSigning(PdfSigningTestBase):
    """2026-07-28 hosts without apps sign through an InputRequiredResult."""

    _run_enhancer = TestElicitationFallback._run_enhancer

    def _ask(self, monkeypatch):
        monkeypatch.setenv("MCP_DISABLE_APPS", "1")
        opened = self._open(make_acroform_pdf())
        first, _ = self._run_enhancer(opened.doc_id, _modern_ctx())
        assert isinstance(first, InputRequiredResult)
        assert first.request_state == f"pdf_sign:{opened.doc_id}"
        prompt = first.input_requests["signature"].params
        assert "full legal name" in prompt.message
        assert set(prompt.requested_schema["properties"]) == {"full_name", "consent"}
        doc = repo.load_document(opened.doc_id, "u1")
        assert doc.status == repo.PDF_STATUS_AWAITING_SIGNATURE
        return opened.doc_id, first.request_state

    def _retry(self, doc_id, state, answer):
        return self._run_enhancer(
            doc_id, _modern_ctx(request_state=state, answer=answer)
        )[0]

    def test_accepted_answer_signs(self, monkeypatch):
        doc_id, state = self._ask(monkeypatch)
        content: _Answer = {"full_name": "Eito Miyamura", "consent": True}
        result = self._retry(
            doc_id, state, ElicitResult(action="accept", content=content)
        )
        assert result.status == "signed"
        last = (repo.load_document(doc_id, "u1").audit or [])[-1]
        assert last["channel"] == "elicitation"
        assert last["confirmed_via_elicitation"] is True
        assert last["typed_name"] == "Eito Miyamura"

    def test_decline_reopens_document(self, monkeypatch):
        doc_id, state = self._ask(monkeypatch)
        result = self._retry(doc_id, state, ElicitResult(action="decline"))
        assert result.status == "signing_declined"
        assert repo.load_document(doc_id, "u1").status == repo.PDF_STATUS_OPEN

    def test_malformed_answer_signs_nothing(self, monkeypatch):
        doc_id, state = self._ask(monkeypatch)
        content: _Answer = {"full_name": "Eito Miyamura"}  # consent missing
        result = self._retry(
            doc_id, state, ElicitResult(action="accept", content=content)
        )
        assert result.status == "signing_declined"
        assert repo.load_document(doc_id, "u1").status == repo.PDF_STATUS_OPEN

    def test_answer_for_another_document_asks_again(self, monkeypatch):
        doc_id, _ = self._ask(monkeypatch)
        content: _Answer = {"full_name": "Eito Miyamura", "consent": True}
        result = self._retry(
            doc_id, "pdf_sign:other", ElicitResult(action="accept", content=content)
        )
        assert isinstance(result, InputRequiredResult)
        doc = repo.load_document(doc_id, "u1")
        assert doc.status == repo.PDF_STATUS_AWAITING_SIGNATURE


class TestPdfExport(PdfSigningTestBase):
    _DEST = GmailDraftDestination(draft_id="d1")

    def _export(self, doc_id: str, destination=None):
        return pdf_export(
            PdfExportInput(
                user_id="u1", doc_id=doc_id, destination=destination or self._DEST
            )
        )

    def _stub_delivery(self):
        delivered: dict = {}

        def _handler(user_id, destination, filename, data):
            delivered.update(
                user_id=user_id,
                draft_id=destination.draft_id,
                filename=filename,
                data=data,
            )
            return PdfDelivery(
                ref_id=destination.draft_id,
                attachments=[
                    PdfExportedAttachment(
                        filename=filename,
                        mime_type="application/pdf",
                        size=len(data),
                        attachment_id="att-1",
                    )
                ],
            )

        return delivered, patch.object(
            pdf_forms_svc, "deliver_to_destination", side_effect=_handler
        )

    def test_export_filled_document_defaults_filename(self):
        opened = self._open(make_acroform_pdf())
        delivered, stub = self._stub_delivery()
        with stub:
            result = self._export(opened.doc_id)
        assert result.status == "open"
        assert result.filename == "nda-filled.pdf"
        assert result.destination_type == "gmail_draft"
        assert result.draft_id == "d1"
        assert result.attachments[0].attachment_id == "att-1"
        assert delivered["data"].startswith(b"%PDF-")
        doc = repo.load_document(opened.doc_id, "u1")
        assert (doc.audit or [])[-1]["event"] == "exported"

    def test_export_signed_document_defaults_signed_filename(self):
        doc_id = self._open_awaiting()
        self._sign(doc_id)
        delivered, stub = self._stub_delivery()
        with stub:
            result = self._export(doc_id)
        assert result.status == "signed"
        assert result.filename == "nda-signed.pdf"
        # The delivered bytes are the sealed ones.
        assert b"MyMCPSignatureAudit" in delivered["data"]

    def test_export_filename_override(self):
        opened = self._open(make_acroform_pdf())
        _, stub = self._stub_delivery()
        with stub:
            result = self._export(
                opened.doc_id,
                GmailDraftDestination(draft_id="d1", filename="custom.pdf"),
            )
        assert result.filename == "custom.pdf"

    def test_export_result_never_contains_bytes(self):
        opened = self._open(make_acroform_pdf())
        _, stub = self._stub_delivery()
        with stub:
            result = self._export(opened.doc_id)
        assert "data_base64" not in result.model_dump_json()
        assert "JVBERi" not in result.model_dump_json()

    def test_export_rejected_while_awaiting_signature(self):
        doc_id = self._open_awaiting()
        with pytest.raises(PdfExportStateError):
            self._export(doc_id)
