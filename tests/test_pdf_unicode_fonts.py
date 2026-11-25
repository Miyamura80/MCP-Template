"""Unicode overlay/stamp text (issue #204): embedded Noto subsets.

Non-Latin-1 names and add_text overlays must render with real glyphs from an
embedded font, stay extractable (ToUnicode), and leave Latin-1 output on the
standard-14 path. Characters no font covers are still refused up front.
"""

import hashlib
import io
import re
import unicodedata
from typing import Any
from unittest.mock import patch

import pypdfium2 as pdfium
import pytest
from pypdf import PdfReader, PdfWriter

from common import global_config
from models.pdf_forms import AddTextOp, PdfEditInput, SignaturePlacement
from services import pdf_documents_repo as repo
from services import pdf_fonts, pdf_forms_svc, pdf_signing
from services.pdf_edit_engine import PdfEditBatchError
from services.pdf_overlay import (
    OverlayText,
    build_text_overlay_page,
    fit_font_size,
    text_width,
    unrenderable_pdf_text,
    unrenderable_reason,
)
from tests.pdf_fixtures import make_flat_pdf
from tests.pdf_harness import PdfSigningTestBase
from tests.test_template import TestTemplate

NAMES = ["山田太郎", "宮村英人", "王小明", "Ελένη", "Иван", "Nguyễn Văn An"]
# Opt-in only: Hangul, and a simplified-only hanzi the bundled JP subset lacks.
KO_ZH = "김们"
KR_FONT = "NotoSansKR-Regular.subset.ttf"


@pytest.fixture(autouse=True)
def _no_optional_fonts(tmp_path):
    """Default chain = committed fonts only, even if `make pdf_fonts_cjk` ran."""
    empty = tmp_path / "no-optional-fonts"
    empty.mkdir()
    with patch.object(pdf_fonts, "OPTIONAL_DIR", empty):
        yield


def _tiny_font(path, chars: str) -> str:
    """Write a minimal TrueType font with a box glyph per char; return sha256."""
    from fontTools.fontBuilder import FontBuilder  # noqa: PLC0415 - test-only
    from fontTools.pens.ttGlyphPen import TTGlyphPen  # noqa: PLC0415

    names = [".notdef", *(f"g{i}" for i in range(len(chars)))]
    pen = TTGlyphPen(None)
    pen.moveTo((50, 0))
    pen.lineTo((50, 700))
    pen.lineTo((550, 700))
    pen.lineTo((550, 0))
    pen.closePath()
    box = pen.glyph()
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({ord(c): f"g{i}" for i, c in enumerate(chars)})
    builder.setupGlyf(dict.fromkeys(names, box))
    builder.setupHorizontalMetrics(dict.fromkeys(names, (600, 50)))
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {"familyName": "TinyTest", "styleName": "Regular", "psName": "TinyTest"}
    )
    builder.setupOS2()
    builder.setupPost()
    builder.save(str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


ARABIC_NAME = "محمد علي"
VOCALIZED = "مُحَمَّد"
RLO_SPOOF = "John\u202eeod"  # displays as "John doe" in bidi-aware viewers


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


def _to_unicode(font) -> dict[int, str]:
    """Parse a font's /ToUnicode CMap (bfchar + array-form bfrange)."""
    cmap = font["/ToUnicode"].get_object().get_data().decode("ascii")
    out: dict[int, str] = {}

    def text(hexstr: str) -> str:
        return bytes.fromhex(hexstr).decode("utf-16-be")

    for block in re.findall(r"beginbfchar(.*?)endbfchar", cmap, re.S):
        for cid, dst in re.findall(r"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]*)>", block):
            out[int(cid, 16)] = text(dst)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", cmap, re.S):
        for lo, _hi, targets in re.findall(
            r"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>\s*\[(.*?)\]", block
        ):
            for offset, dst in enumerate(re.findall(r"<([0-9a-fA-F]*)>", targets)):
                out[int(lo, 16) + offset] = text(dst)
    return out


def _arabic_font(data: bytes):
    fonts = _fonts_on_page(data)
    return next(f for name, f in fonts.items() if "NotoSansArabic" in name)


def _clusters(word: str) -> list[str]:
    """Split into base letter + following combining marks."""
    out: list[str] = []
    for char in word:
        if out and unicodedata.combining(char):
            out[-1] += char
        else:
            out.append(char)
    return out


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

    @pytest.mark.parametrize(
        "char",
        ["\u061c", "\u200b", "\u200e", "\u200f", "\u202a", "\u202e"]
        + ["\u2066", "\u2069", "\ufeff"],
    )
    def test_bidi_and_invisible_controls_rejected(self, char):
        assert unrenderable_pdf_text(f"John{char}eod") == char

    def test_joiners_allowed(self):
        assert unrenderable_pdf_text("می\u200cخواهم") == ""
        assert unrenderable_pdf_text("محمد\u200d") == ""

    def test_reason_names_only_the_actual_failure(self):
        control = unrenderable_reason(RLO_SPOOF) or ""
        assert "control" in control
        assert "cover" not in control
        uncovered = unrenderable_reason("שרה") or ""
        assert "don't cover" in uncovered
        assert "transliterate" in uncovered
        assert pdf_fonts.coverage_summary() in uncovered
        assert "control" not in uncovered
        # The operator remedy is for logs/docs, not the model.
        assert "extra_font_paths" not in uncovered
        assert unrenderable_reason("山田太郎") is None

    def test_vendored_fonts_match_recorded_hashes(self):
        for name, expected in pdf_fonts.BUNDLED_FONTS.items():
            data = (pdf_fonts.FONT_DIR / name).read_bytes()
            assert hashlib.sha256(data).hexdigest() == expected, name

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
        rtl = [r.text for r in runs if r.rtl and r.text.strip()]
        assert rtl == ["علي", "محمد"]

    def test_extra_font_paths_extend_coverage(self, tmp_path):
        font = tmp_path / "tiny.ttf"
        _tiny_font(font, KO_ZH)
        assert unrenderable_pdf_text(KO_ZH) == KO_ZH
        fonts_cfg = global_config.pdf_forms.fonts
        with patch.object(fonts_cfg, "extra_font_paths", [str(font)]):
            assert unrenderable_pdf_text(KO_ZH) == ""

    def test_unreadable_extra_font_is_skipped(self):
        fonts_cfg = global_config.pdf_forms.fonts
        with patch.object(fonts_cfg, "extra_font_paths", ["/nonexistent/font.ttf"]):
            assert len(pdf_fonts.font_chain()) == len(pdf_fonts.BUNDLED_FONTS)
            assert unrenderable_pdf_text("שרה") == "שרה"


class TestOptionalCjkFonts(TestTemplate):
    """Chinese/Korean are opt-in (`make pdf_fonts_cjk`), not bundled."""

    def test_refused_by_default(self):
        assert unrenderable_pdf_text("김민준") == "김민준"
        assert unrenderable_pdf_text("们") == "们"
        assert "Korean" not in pdf_fonts.coverage_summary()

    def test_optional_dir_font_is_picked_up(self, tmp_path):
        sha = _tiny_font(tmp_path / KR_FONT, KO_ZH)
        pinned = {KR_FONT: (sha, "common Korean Hangul")}
        with (
            patch.object(pdf_fonts, "OPTIONAL_DIR", tmp_path),
            patch.object(pdf_fonts, "OPTIONAL_FONTS", pinned),
        ):
            assert unrenderable_pdf_text(KO_ZH) == ""
            assert "Korean" in pdf_fonts.coverage_summary()
            data = _overlay_pdf([OverlayText(50.0, 700.0, 14.0, KO_ZH)])
            assert KO_ZH in PdfReader(io.BytesIO(data)).pages[0].extract_text()

    def test_optional_font_with_wrong_hash_is_ignored(self, tmp_path):
        _tiny_font(tmp_path / KR_FONT, KO_ZH)
        pinned = {KR_FONT: ("0" * 64, "common Korean Hangul")}
        with (
            patch.object(pdf_fonts, "OPTIONAL_DIR", tmp_path),
            patch.object(pdf_fonts, "OPTIONAL_FONTS", pinned),
        ):
            assert unrenderable_pdf_text(KO_ZH) == KO_ZH

    def test_real_opt_in_fonts_when_built(self):
        real_dir = pdf_fonts.FONT_DIR / "optional"
        if not all((real_dir / name).is_file() for name in pdf_fonts.OPTIONAL_FONTS):
            pytest.skip("opt-in CJK fonts not built (run `make pdf_fonts_cjk`)")
        with patch.object(pdf_fonts, "OPTIONAL_DIR", real_dir):
            assert unrenderable_pdf_text("김민준 们") == ""
            data = _overlay_pdf([OverlayText(50.0, 700.0, 14.0, "김민준 们")])
            assert "김민준 们" in PdfReader(io.BytesIO(data)).pages[0].extract_text()


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
        for name in embedded:
            font = fonts[name]
            assert font["/Subtype"] == "/Type0"
            assert "/ToUnicode" in font
            # A per-document subset, far smaller than the bundled file.
            assert 0 < len(_embedded_font_bytes(font)) < 200_000
        # Glyphs actually paint: the first name's line is not blank.
        assert _ink_ratio(data, (50, 696, 110, 712)) > 0.05

    def test_arabic_to_unicode_cmap_is_exact(self):
        # One entry per joined letter form, each mapping to its own letter
        # (no empty entries from pyHanko's RTL cluster bug).
        data = _overlay_pdf([OverlayText(50.0, 700.0, 14.0, ARABIC_NAME)])
        cmap = _to_unicode(_arabic_font(data))
        assert sorted(cmap.values()) == sorted(ARABIC_NAME.replace(" ", ""))
        assert _ink_ratio(data, (50, 696, 110, 712)) > 0.03

    def test_vocalized_arabic_maps_every_base_letter(self):
        # Vowel marks share a cluster with their letter: the cluster's text
        # must land on the base glyph (GDEF class), never on a mark glyph.
        data = _overlay_pdf([OverlayText(50.0, 700.0, 14.0, VOCALIZED)])
        values = sorted(_to_unicode(_arabic_font(data)).values())
        assert values == sorted(_clusters(VOCALIZED))

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


class TestStampFitting(TestTemplate):
    def test_fit_shrinks_then_refuses(self):
        assert fit_font_size("Ann Lee", 14.0, 200.0, 8.0) == 14.0
        long_name = "Wolfeschlegelsteinhausenbergerdorff Hubert Blaine"
        size = fit_font_size(long_name, 14.0, 200.0, 8.0)
        assert size is not None and 8.0 <= size < 14.0
        assert text_width(long_name, size) <= 200.0 + 1e-6
        assert fit_font_size(long_name * 3, 14.0, 200.0, 8.0) is None


class TestUnicodeSigningAndEditing(PdfSigningTestBase):
    def test_bidi_spoofed_name_refused(self):
        doc_id = self._open_awaiting()
        with pytest.raises(pdf_signing.PdfSigningInputError) as exc:
            self._sign(doc_id, typed_name=RLO_SPOOF)
        assert "control" in str(exc.value)
        assert (
            repo.load_document(doc_id, "u1").status
            == repo.PDF_STATUS_AWAITING_SIGNATURE
        )

    def test_overlong_name_refused(self):
        doc_id = self._open_awaiting()
        with pytest.raises(pdf_signing.PdfSigningInputError) as exc:
            self._sign(doc_id, typed_name="Hubert Blaine Wolfeschlegel " * 4)
        assert "too long" in str(exc.value)

    def test_long_name_stamp_is_shrunk_to_fit(self):
        name = "Wolfeschlegelsteinhausenbergerdorff Hubert Blaine"
        doc_id = self._open_awaiting()
        doc, _ = self._sign(doc_id, typed_name=name)
        assert name in PdfReader(io.BytesIO(doc.current_bytes)).pages[0].extract_text()
        assert b"/MyMCPSigF1 14.00 Tf" not in doc.current_bytes

    def test_add_text_bidi_spoof_rejected(self):
        opened = self._open(make_flat_pdf())
        with pytest.raises(PdfEditBatchError) as exc:
            pdf_forms_svc.pdf_edit(
                PdfEditInput(
                    user_id="u1",
                    doc_id=opened.doc_id,
                    ops=[AddTextOp(page=1, x=110.0, y=650.0, text=RLO_SPOOF)],
                )
            )
        assert "control" in exc.value.errors[0]["error"]

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
        cmap = _to_unicode(_arabic_font(doc.current_bytes))
        assert sorted(cmap.values()) == sorted(ARABIC_NAME.replace(" ", ""))

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
