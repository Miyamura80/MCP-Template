"""Tests for the LLM-visible pdf_* services (US-002 onward).

The Gmail bridge is stubbed at the port layer (``resolve_source``) and the
document store runs on in-memory SQLite, so these tests exercise the real
pypdf/pypdfium2 paths end-to-end without Gmail or Postgres.
"""

import base64
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from common import global_config
from db.base import Base
from models.pdf_forms import GmailAttachmentSource, PdfOpenInput
from services import pdf_documents_repo as repo
from services import pdf_forms_svc
from services.pdf_inspect import PdfNotAPdfError
from services.pdf_ports import ResolvedPdfSource
from services.pdf_render import PdfRenderRequestError
from tests.pdf_fixtures import make_acroform_pdf, make_flat_pdf
from tests.test_template import TestTemplate

_SOURCE = GmailAttachmentSource(message_id="m1", attachment_id="a1")


class PdfServiceTestBase(TestTemplate):
    """SQLite-backed harness with the source port stubbed to fixture bytes."""

    def setup_method(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

        @contextmanager
        def _ctx():
            session = self.SessionLocal()
            try:
                yield session
            finally:
                session.close()

        self._patchers = [patch.object(repo, "use_db_session", _ctx)]
        for p in self._patchers:
            p.start()

    def teardown_method(self):
        for p in self._patchers:
            p.stop()

    def _stub_source(self, data: bytes, filename: str = "nda.pdf"):
        return patch.object(
            pdf_forms_svc,
            "resolve_source",
            lambda user_id, source: ResolvedPdfSource(filename=filename, data=data),
        )

    def _open(self, data: bytes, filename: str = "nda.pdf", **kwargs):
        with self._stub_source(data, filename):
            return pdf_forms_svc.pdf_open(
                PdfOpenInput(user_id="u1", source=_SOURCE, **kwargs)
            )


class TestPdfOpen(PdfServiceTestBase):
    def test_acroform_inventory(self):
        result = self._open(make_acroform_pdf())
        assert result.doc_id
        assert result.status == "open"
        assert result.filename == "nda.pdf"
        assert result.page_count == 1
        assert result.has_acroform is True
        by_name = {f.name: f for f in result.fields}
        assert by_name["full_name"].field_type == "text"
        assert by_name["full_name"].required is True
        assert by_name["full_name"].rect == [100.0, 600.0, 300.0, 620.0]
        assert by_name["full_name"].page == 1
        assert by_name["agree_terms"].field_type == "checkbox"
        assert set(by_name["agree_terms"].options) == {"/Yes", "/Off"}
        assert by_name["agree_terms"].value == "/Off"
        assert by_name["state"].field_type == "choice"
        assert by_name["state"].options == ["CA", "NY"]
        assert by_name["signature"].field_type == "signature"
        # AcroForm docs are filled by field name; no layout needed.
        assert result.text_layout == []

    def test_flat_pdf_text_layout(self):
        result = self._open(make_flat_pdf())
        assert result.has_acroform is False
        assert result.fields == []
        assert result.page_sizes[0].width == 612.0
        assert result.page_sizes[0].height == 792.0
        lines = {line.text: line for line in result.text_layout}
        assert "NON-DISCLOSURE AGREEMENT" in lines
        title = lines["NON-DISCLOSURE AGREEMENT"]
        assert title.page == 1
        assert title.x == pytest.approx(72.0)
        assert title.y == pytest.approx(700.0)
        assert "Signature:" in lines

    def test_text_layout_cap_sets_truncated(self):
        with patch.object(global_config.pdf_forms, "text_layout_max_lines", 2):
            result = self._open(make_flat_pdf())
        assert len(result.text_layout) == 2
        assert result.text_layout_truncated is True

    def test_open_persists_session(self):
        result = self._open(make_flat_pdf())
        doc = repo.load_document(result.doc_id, "u1")
        assert doc.status == repo.PDF_STATUS_OPEN
        assert doc.current_bytes.startswith(b"%PDF-")
        assert (doc.source_ref or {}).get("type") == "gmail_attachment"
        assert doc.page_count == 1

    def test_non_pdf_names_detected_type(self):
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        with pytest.raises(PdfNotAPdfError) as exc:
            self._open(png, filename="photo.png")
        assert "image/png" in str(exc.value)

    def test_render_pages_returns_png(self):
        result = self._open(make_flat_pdf(), render_pages=[1])
        assert len(result.page_images) == 1
        image = result.page_images[0]
        assert image.page == 1
        assert image.mime_type == "image/png"
        decoded = base64.b64decode(image.data_base64)
        assert decoded.startswith(b"\x89PNG\r\n\x1a\n")

    def test_render_pages_out_of_range(self):
        with pytest.raises(PdfRenderRequestError):
            self._open(make_flat_pdf(), render_pages=[5])

    def test_render_pages_over_cap(self):
        with (
            patch.object(global_config.pdf_forms, "render_max_pages", 2),
            pytest.raises(PdfRenderRequestError),
        ):
            self._open(make_flat_pdf(page_count=4), render_pages=[1, 2, 3])
