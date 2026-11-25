"""Canonical text-overlay machinery, shared by edit and signing.

Part of the PDF core (isolation seam): no Gmail imports.

Both ``pdf_edit``'s add_text ops and the signature stamp draw text by merging
a synthesized overlay page into the target page's content stream -
content-stream text renders identically in every viewer/rasterizer and
survives flattening, unlike annotation appearances. This module is the ONLY
place that builds those overlays.

Two font paths, chosen per line:

- Latin-1 text keeps the standard-14 Helvetica faces (``/WinAnsiEncoding``):
  nothing embedded, byte-identical to earlier output.
- Anything else is shaped with HarfBuzz and drawn with an embedded subset of
  the Unicode font chain in :mod:`services.pdf_fonts` (Type0/CIDFont with a
  ToUnicode CMap, so the text stays extractable and searchable).

The overlay PDF is written with pyHanko's writer, whose ``GlyphAccumulator``
does the shaping and per-document subsetting; pypdf then reads it back so
callers can ``merge_page`` it as before.
"""

from __future__ import annotations

import functools
import io
from collections.abc import Sequence
from dataclasses import dataclass

import uharfbuzz as hb
from loguru import logger as log
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.font.opentype import GlyphAccumulator
from pyhanko.pdf_utils.writer import PdfFileWriter
from pypdf import PdfReader

from services.pdf_fonts import (
    FontFace,
    coverage_summary,
    split_runs,
    uncovered_chars,
)
from services.pdf_fonts.bidi import BIDI_CONTROLS

# GDEF GlyphClassDef value for combining marks.
_GDEF_MARK_CLASS = 3

# tan(12 deg): Helvetica-Oblique's slant, reused as a synthetic oblique for
# embedded faces so a non-Latin signature still reads as "the signed name".
_OBLIQUE_SKEW = 0.2126


@dataclass(frozen=True)
class OverlayText:
    """One line of overlay text at a baseline position (PDF user space)."""

    x: float
    y: float
    size: float
    text: str
    oblique: bool = False


def _is_control(code: int) -> bool:
    # C0/C1 controls, plus bidi overrides and invisible zero-width characters
    # that could make a stamped name display differently from what was typed.
    return code < 0x20 or 0x7F <= code <= 0x9F or code in BIDI_CONTROLS


def _is_latin1_printable(text: str) -> bool:
    return all(not _is_control(ord(c)) and ord(c) <= 0xFF for c in text)


def unrenderable_pdf_text(text: str) -> str:
    """Characters in ``text`` no overlay font can render, deduped.

    Callers MUST reject text containing these up front: letting them through
    would stamp an empty ``.notdef`` box - unacceptable for a signature stamp
    on a legal document, and bad even for a plain form overlay. Control
    characters (C0/C1, bidi controls, zero-width space/BOM) are always
    rejected; anything else is fine as long as some face in the font chain
    covers it. Returns "" when fully renderable.
    """
    if _is_latin1_printable(text):
        return ""
    seen: dict[str, None] = {c: None for c in text if _is_control(ord(c))}
    for char in uncovered_chars(text):
        seen.setdefault(char)
    return "".join(seen)


def unrenderable_reason(text: str) -> str | None:
    """Model-facing explanation of why ``text`` cannot be drawn, or None.

    Mentions control characters only when they are present, and points at
    transliteration for uncovered scripts. The operator-side remedy
    (``pdf_forms.fonts.extra_font_paths``) goes to the log, not the model.
    """
    bad = unrenderable_pdf_text(text)
    if not bad:
        return None
    controls = "".join(c for c in bad if _is_control(ord(c)))
    uncovered = "".join(c for c in bad if not _is_control(ord(c)))
    reasons = []
    if controls:
        reasons.append(
            f"contains invisible control or text-direction characters "
            f"{controls!r}, which are not allowed"
        )
    if uncovered:
        log.info(
            "PDF overlay refused characters {!r} no font covers; add a font "
            "via pdf_forms.fonts.extra_font_paths to support them.",
            uncovered,
        )
        reasons.append(
            f"contains characters the server's fonts don't cover: "
            f"{uncovered!r} (supported: {coverage_summary()}); transliterate "
            "or remove them"
        )
    return "; ".join(reasons)


class _Accumulator(GlyphAccumulator):
    """TEMPORARY SHIM over pyHanko's accumulator: fix RTL ToUnicode maps.

    pyHanko (0.35.x) derives each glyph's source text as
    ``txt[cluster:next_cluster]`` in output order, which assumes ascending
    clusters. HarfBuzz emits RTL glyphs in visual order (descending
    clusters), so every Arabic glyph would map to "" and the run would not
    extract or search. Reshape the run and remap each cluster's logical span
    onto its base glyph instead. Relies on pyHanko internals
    (``_get_cid_and_width``, ``_cid_to_unicode``, ``hb_font``), hence the
    ``<0.36`` pin in pyproject.toml; drop this class once pyHanko maps RTL
    clusters itself (tests/test_pdf_unicode_fonts.py pins the behaviour).
    """

    @functools.cached_property
    def _glyph_classes(self) -> dict[str, int]:
        if "GDEF" not in self.tt:
            return {}
        class_def = self.tt["GDEF"].table.GlyphClassDef
        return dict(class_def.classDefs) if class_def is not None else {}

    def _base_glyph(self, gids: list[int]) -> int | None:
        """First non-mark glyph of a cluster (marks carry no text of their own)."""
        real = [g for g in gids if g != 0]
        for gid in real:
            name = self.tt.getGlyphName(gid)
            if self._glyph_classes.get(name) != _GDEF_MARK_CLASS:
                return gid
        return real[0] if real else None

    def shape(self, txt: str, with_actual_text: bool = True):
        result = super().shape(txt, with_actual_text)
        if self.writing_direction == "rtl":
            self._remap_rtl(txt)
        return result

    def _remap_rtl(self, txt: str) -> None:
        # uharfbuzz is a compiled extension without type stubs.
        buf = hb.Buffer()  # ty: ignore[unresolved-attribute]
        buf.add_str(txt)
        buf.direction = "rtl"
        buf.guess_segment_properties()
        hb.shape(self.hb_font, buf, self.features)  # ty: ignore[unresolved-attribute]
        gids_by_cluster: dict[int, list[int]] = {}
        for info in buf.glyph_infos:
            gids_by_cluster.setdefault(info.cluster, []).append(info.codepoint)
        starts = sorted(gids_by_cluster)
        ends = [*starts[1:], len(txt)]
        for start, end in zip(starts, ends, strict=True):
            # A multi-glyph cluster (letter + vowel marks) maps its whole text
            # to the base glyph only: mapping it to every glyph would
            # duplicate it on extraction.
            gid = self._base_glyph(gids_by_cluster[start])
            if gid is None:
                continue
            for other in gids_by_cluster[start]:
                if other not in (0, gid):
                    # pyHanko's fallback maps mark glyphs to the bare mark;
                    # drop that so the vowel is not extracted twice.
                    other_cid, _ = self._get_cid_and_width(other)
                    self._cid_to_unicode.pop(other_cid, None)
            cid, _ = self._get_cid_and_width(gid)
            self._cid_to_unicode[cid] = txt[start:end]


@functools.cache
def _hb_font(face: FontFace):
    # uharfbuzz is a compiled extension without type stubs.
    return hb.Font(hb.Face(face.data))  # ty: ignore[unresolved-attribute]


def text_width(text: str, size: float) -> float:
    """Shaped advance width of ``text`` at ``size`` points.

    Measured with the Unicode chain even for Latin-1 text (drawn in
    Helvetica): Noto Sans runs slightly wider than Helvetica, so the
    estimate errs toward fitting. ``text`` must be renderable.
    """
    total = 0.0
    for run in split_runs(text):
        font = _hb_font(run.face)
        buf = hb.Buffer()  # ty: ignore[unresolved-attribute]
        buf.add_str(run.text)
        buf.direction = "rtl" if run.rtl else "ltr"
        buf.guess_segment_properties()
        hb.shape(font, buf)  # ty: ignore[unresolved-attribute]
        advance = sum(pos.x_advance for pos in buf.glyph_positions)
        total += advance / font.face.upem * size
    return total


def fit_font_size(
    text: str, preferred: float, max_width: float, minimum: float
) -> float | None:
    """Largest size <= ``preferred`` at which ``text`` fits ``max_width``.

    Returns None when even ``minimum`` overflows.
    """
    width = text_width(text, preferred)
    if width <= max_width:
        return preferred
    size = preferred * max_width / width  # width scales linearly with size
    return size if size >= minimum else None


def _escape_literal(text: str) -> bytes:
    escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return escaped.encode("latin-1")


class _OverlayFonts:
    """Font resources for one overlay page, created on first use."""

    def __init__(self, writer: PdfFileWriter, prefix: str) -> None:
        self._writer = writer
        self._prefix = prefix
        self.resources = generic.DictionaryObject()
        self._type1: dict[str, bytes] = {}
        self._embedded: dict[str, tuple[bytes, _Accumulator]] = {}

    def _next_name(self) -> str:
        return f"/{self._prefix}F{len(self.resources) + 1}"

    def type1(self, base_font: str) -> bytes:
        """Resource name for a standard-14 face.

        ``/WinAnsiEncoding`` is declared explicitly: without it, viewers
        apply StandardEncoding, which maps the upper Latin-1 range to other
        glyphs (e.g. 0xC0 'À' renders as '¿').
        """
        if base_font not in self._type1:
            name = self._next_name()
            self.resources[generic.pdf_name(name)] = self._writer.add_object(
                generic.DictionaryObject(
                    {
                        generic.pdf_name("/Type"): generic.pdf_name("/Font"),
                        generic.pdf_name("/Subtype"): generic.pdf_name("/Type1"),
                        generic.pdf_name("/BaseFont"): generic.pdf_name(base_font),
                        generic.pdf_name("/Encoding"): generic.pdf_name(
                            "/WinAnsiEncoding"
                        ),
                    }
                )
            )
            self._type1[base_font] = name.encode("ascii")
        return self._type1[base_font]

    def embedded(self, face: FontFace) -> tuple[bytes, _Accumulator]:
        """Resource name + shaping engine for an embedded Unicode face.

        Engines shape at size 1; the caller scales with the text matrix, so
        one subset per face serves every font size on the page.
        """
        key = str(face.path)
        if key not in self._embedded:
            name = self._next_name()
            engine = _Accumulator(self._writer, io.BytesIO(face.data), 1)
            self.resources[generic.pdf_name(name)] = engine.as_resource()
            self._embedded[key] = (name.encode("ascii"), engine)
        return self._embedded[key]


def _type1_line(fonts: _OverlayFonts, item: OverlayText) -> bytes:
    name = fonts.type1("/Helvetica-Oblique" if item.oblique else "/Helvetica")
    return b"BT %s %.2f Tf %.2f %.2f Td (%s) Tj ET\n" % (
        name,
        item.size,
        item.x,
        item.y,
        _escape_literal(item.text),
    )


def _embedded_line(fonts: _OverlayFonts, item: OverlayText) -> bytes:
    skew = _OBLIQUE_SKEW * item.size if item.oblique else 0.0
    ops = [
        b"BT %.2f 0 %.4f %.2f %.2f %.2f Tm"
        % (item.size, skew, item.size, item.x, item.y)
    ]
    for run in split_runs(item.text):
        name, engine = fonts.embedded(run.face)
        # Runs are direction-homogeneous; HarfBuzz emits RTL runs in visual
        # order, and each run's trailing Td advances to the next one.
        engine.writing_direction = "rtl" if run.rtl else "ltr"
        ops.append(b"%s 1 Tf %s" % (name, engine.shape(run.text).graphics_ops))
    ops.append(b"ET\n")
    return b" ".join(ops)


def build_text_overlay_page(
    width: float,
    height: float,
    items: Sequence[OverlayText],
    *,
    font_prefix: str,
):
    """Build a single merge_page()-ready page drawing ``items``.

    ``font_prefix`` namespaces the font resource names (e.g. ``MyMCPSig`` ->
    ``/MyMCPSigF1``) so they are recognisable in the merged page. Items must
    have passed :func:`unrenderable_pdf_text`.
    """
    writer = PdfFileWriter(stream_xrefs=False)
    fonts = _OverlayFonts(writer, font_prefix)
    content = b"".join(
        _type1_line(fonts, item)
        if _is_latin1_printable(item.text)
        else _embedded_line(fonts, item)
        for item in items
    )
    page = generic.DictionaryObject(
        {
            generic.pdf_name("/Type"): generic.pdf_name("/Page"),
            generic.pdf_name("/MediaBox"): generic.ArrayObject(
                [generic.FloatObject(v) for v in (0, 0, width, height)]
            ),
            generic.pdf_name("/Resources"): generic.DictionaryObject(
                {generic.pdf_name("/Font"): fonts.resources}
            ),
            generic.pdf_name("/Contents"): writer.add_object(
                generic.StreamObject(stream_data=content)
            ),
        }
    )
    writer.insert_page(page)
    buffer = io.BytesIO()
    writer.write(buffer)
    return PdfReader(io.BytesIO(buffer.getvalue())).pages[0]
