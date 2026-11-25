"""Unicode overlay/stamp text (issue #204): embedded Noto subsets.

Non-Latin-1 names and add_text overlays must render with real glyphs from an
embedded font, stay extractable (ToUnicode), and leave Latin-1 output on the
standard-14 path. Characters no font covers are still refused up front.
"""

import io
from typing import Any
from unittest.mock import patch

import pypdfium2 as pdfium
import pytest
from pypdf import PdfReader, PdfWriter

from common import global_config
from models.pdf_forms import AddTextOp, PdfEditInput, SignaturePlacement
from services import pdf_documents_repo as repo
from services import pdf_fonts, pdf_forms_svc
from services.pdf_overlay import (
    OverlayText,
    build_text_overlay_page,
    unrenderable_pdf_text,
)
from tests.pdf_fixtures import make_flat_pdf
from tests.pdf_harness import PdfSigningTestBase
from tests.test_template import TestTemplate

NAMES = ["山田太郎", "宮村英人", "王小明", "Ελένη", "Иван", "김민준", "Nguyễn Văn An"]
ARABIC_NAME = "محمد علي"


def _overlay_pdf(items: list[OverlayText]) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    page.merge_page(build_text_overlay_page(612, 792, items, font_prefix="T"))
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _fonts_on_page(data: bytes) -> dict[str, Any]:
    resources: Any = PdfReader(io.BytesIO(data)).pages[0]["/Resources"]
    return {
        str(font["/BaseFont"]): font
        for font in (ref.get_object() for ref in resources["/Font"].values())
    }


def _embedded_font_bytes(font) -> bytes:
    descriptor = font["/DescendantFonts"][0].get_object()["/FontDescriptor"]
    return descriptor["/FontFile2"].get_object().get_data()


def _ink_ratio(data: bytes, box: tuple[float, float, float, float]) -> float:
    """Fraction of non-white pixels inside ``box`` (PDF points) at 2x scale."""
    page = pdfium.PdfDocument(data)[0]
    image = page.render(scale=2).to_pil().convert("L")
    x0, y0, x1, y1 = box
    height = page.get_height()
    crop = image.crop((2 * x0, 2 * (height - y1), 2 * x1, 2 * (height - y0)))
    pixels = list(crop.getdata())
    return sum(1 for p in pixels if p < 128) / len(pixels)


class TestFontChain(TestTemplate):
    @pytest.mark.parametrize("text", [*NAMES, ARABIC_NAME, "Renée Müller"])
    def test_covered_text_is_renderable(self, text):
        assert unrenderable_pdf_text(text) == ""

    def test_uncovered_and_control_chars_reported(self):
        assert unrenderable_pdf_text("שרה") == "שרה"
        assert unrenderable_pdf_text("Ann 😀") == "😀"
        assert unrenderable_pdf_text("a\x01b") == "\x01"
        assert unrenderable_pdf_text("山田\x07") == "\x07"

    def test_runs_pick_first_covering_face(self):
        runs = pdf_fonts.split_runs("Signed by 山田 on 2026")
        assert [(r.face.path.name, r.text) for r in runs] == [
            ("NotoSans-Regular.subset.ttf", "Signed by "),
            ("NotoSansJP-Regular.subset.ttf", "山田"),
            ("NotoSans-Regular.subset.ttf", " on 2026"),
        ]

    def test_rtl_words_drawn_in_visual_order(self):
        # Base direction is LTR: the first logical Arabic word must be drawn
        # rightmost, so the run list (drawing order) has it last.
        runs = pdf_fonts.split_runs(f"by {ARABIC_NAME} on")
        texts = [r.text for r in runs]
        assert texts[0] == "by "
        assert texts[-1] == " on"
        rtl = [r.text for r in runs if r.rtl]
        assert rtl == ["علي", "محمد"]

    def test_extra_font_paths_extend_coverage(self):
        without_kr = tuple(
            n for n in pdf_fonts.BUNDLED_FONTS if not n.startswith("NotoSansKR")
        )
        kr_path = str(pdf_fonts.FONT_DIR / "NotoSansKR-Regular.subset.ttf")
        fonts_cfg = global_config.pdf_forms.fonts
        with patch.object(pdf_fonts, "BUNDLED_FONTS", without_kr):
            assert unrenderable_pdf_text("김민준") == "김민준"
            with patch.object(fonts_cfg, "extra_font_paths", [kr_path]):
                assert unrenderable_pdf_text("김민준") == ""

    def test_unreadable_extra_font_is_skipped(self):
        fonts_cfg = global_config.pdf_forms.fonts
        with patch.object(fonts_cfg, "extra_font_paths", ["/nonexistent/font.ttf"]):
            assert len(pdf_fonts.font_chain()) == len(pdf_fonts.BUNDLED_FONTS)
            assert unrenderable_pdf_text("שרה") == "שרה"


class TestOverlayRendering(TestTemplate):
    def test_non_latin_names_extract_and_embed_subsets(self):
        items = [
            OverlayText(50.0, 700.0 - 30 * i, 14.0, name, oblique=i % 2 == 0)
            for i, name in enumerate(NAMES)
        ]
        data = _overlay_pdf(items)
        text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
        for name in NAMES:
            assert name in text
        fonts = _fonts_on_page(data)
        embedded = [name for name in fonts if "+NotoSans" in name]
        assert any("NotoSansJP" in n for n in embedded)
        assert any("NotoSansKR" in n for n in embedded)
        for name in embedded:
            font = fonts[name]
            assert font["/Subtype"] == "/Type0"
            assert "/ToUnicode" in font
            # A per-document subset, far smaller than the bundled file.
            assert 0 < len(_embedded_font_bytes(font)) < 200_000
        # Glyphs actually paint: the first name's line is not blank.
        assert _ink_ratio(data, (50, 696, 110, 712)) > 0.05

    def test_arabic_is_shaped_and_extractable(self):
        data = _overlay_pdf([OverlayText(50.0, 700.0, 14.0, ARABIC_NAME)])
        text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
        assert set(ARABIC_NAME.replace(" ", "")) <= set(text)
        assert any("NotoSansArabic" in name for name in _fonts_on_page(data))
        assert _ink_ratio(data, (50, 696, 110, 712)) > 0.03

    def test_latin1_text_keeps_standard14_fonts(self):
        data = _overlay_pdf(
            [
                OverlayText(50.0, 700.0, 14.0, "Renée Müller", oblique=True),
                OverlayText(50.0, 680.0, 8.0, "Signed on 2026-01-01"),
            ]
        )
        fonts = _fonts_on_page(data)
        assert set(fonts) == {"/Helvetica-Oblique", "/Helvetica"}
        assert all(f["/Encoding"] == "/WinAnsiEncoding" for f in fonts.values())
        assert "Renée Müller" in PdfReader(io.BytesIO(data)).pages[0].extract_text()


class TestUnicodeSigningAndEditing(PdfSigningTestBase):
    @pytest.mark.parametrize("name", ["山田太郎", "Ελένη Παπαδοπούλου", "Иван Петров"])
    def test_non_latin_signature_stamps_and_seals(self, name):
        from tests.test_pdf_signing import _validate_seal  # noqa: PLC0415 - reuse

        doc_id = self._open_awaiting()
        doc, audit = self._sign(doc_id, typed_name=name)
        assert doc.status == repo.PDF_STATUS_SIGNED
        assert audit["typed_name"] == name
        text = PdfReader(io.BytesIO(doc.current_bytes)).pages[0].extract_text()
        assert name in text
        assert f"Signed by {name} on" in text
        status = _validate_seal(doc.current_bytes)
        assert status.intact
        assert status.valid

    def test_arabic_signature_on_flat_pdf(self):
        opened = self._open(make_flat_pdf())
        self._request(opened.doc_id, SignaturePlacement(page=1, x=140.0, y=545.0))
        doc, _ = self._sign(opened.doc_id, typed_name=ARABIC_NAME)
        text = PdfReader(io.BytesIO(doc.current_bytes)).pages[0].extract_text()
        assert set(ARABIC_NAME.replace(" ", "")) <= set(text)

    def test_add_text_non_latin_overlay(self):
        opened = self._open(make_flat_pdf())
        result = pdf_forms_svc.pdf_edit(
            PdfEditInput(
                user_id="u1",
                doc_id=opened.doc_id,
                ops=[
                    AddTextOp(page=1, x=110.0, y=650.0, text="宮村英人"),
                    AddTextOp(page=1, x=110.0, y=630.0, text="Иван Петров"),
                ],
            )
        )
        assert result.applied_ops == 2
        data = repo.load_document(opened.doc_id, "u1").current_bytes
        text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
        assert "宮村英人" in text
        assert "Иван Петров" in text
