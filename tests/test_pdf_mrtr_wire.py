"""pdf_request_signature over 2026-07-28: an InputRequiredResult round trip."""

from services import pdf_documents_repo as repo
from tests.pdf_fixtures import make_acroform_pdf
from tests.pdf_harness import PdfSigningTestBase
from tests.test_mcp_e2e import _wire_client
from tests.test_mcp_protocol_eras import _ModernMcpClient


class TestPdfSignatureRoundTrip(PdfSigningTestBase):
    def test_modern_host_without_apps_signs_on_the_retry(self, monkeypatch):
        monkeypatch.setenv("MCP_DISABLE_APPS", "1")
        opened = self._open(make_acroform_pdf())
        params = {
            "name": "pdf_request_signature",
            "arguments": {
                "doc_id": opened.doc_id,
                "placement": {"field_name": "signature"},
            },
        }
        with _wire_client("u1") as (client, key):
            host = _ModernMcpClient(client, key, capabilities={"elicitation": {}})
            first = host.request("tools/call", params)
            assert first["resultType"] == "input_required"
            assert "signature" in first["inputRequests"]
            # Sealed by the SDK: the plaintext doc id is not on the wire.
            assert opened.doc_id not in first["requestState"]
            answer = {"full_name": "Eito Miyamura", "consent": True}
            second = host.request(
                "tools/call",
                {
                    **params,
                    "requestState": first["requestState"],
                    "inputResponses": {
                        "signature": {"action": "accept", "content": answer}
                    },
                },
            )
        assert second["isError"] is False
        assert second["structuredContent"]["status"] == "signed"
        doc = repo.load_document(second["structuredContent"]["doc_id"], "u1")
        assert doc.status == repo.PDF_STATUS_SIGNED
