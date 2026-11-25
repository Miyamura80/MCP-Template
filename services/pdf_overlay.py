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

import io
from collections.abc import Sequence
from dataclasses import dataclass

import uharfbuzz as hb
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.font.opentype import GlyphAccumulator
from pyhanko.pdf_utils.writer import PdfFileWriter
from pypdf import PdfReader

from services.pdf_fonts import FontFace, split_runs, uncovered_chars

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
    return code < 0x20 or 0x7F <= code <= 0x9F


def _is_latin1_printable(text: str) -> bool:
    return all(not _is_control(ord(c)) and ord(c) <= 0xFF for c in text)


def unrenderable_pdf_text(text: str) -> str:
    """Characters in ``text`` no overlay font can render, deduped.

    Callers MUST reject text containing these up front: letting them through
    would stamp an empty ``.notdef`` box - unacceptable for a signature stamp
    on a legal document, and bad even for a plain form overlay. Control
    characters (C0/C1) are always rejected; anything else is fine as long as
    some face in the font chain covers it. Returns "" when fully renderable.
    """
    if _is_latin1_printable(text):
        return ""
    seen: dict[str, None] = {c: None for c in text if _is_control(ord(c))}
    for char in uncovered_chars(text):
        seen.setdefault(char)
    return "".join(seen)


class _Accumulator(GlyphAccumulator):
    """pyHanko's accumulator with a correct ToUnicode map for RTL runs.

    pyHanko derives each glyph's source text as ``txt[cluster:next_cluster]``
    in output order, which assumes ascending clusters. HarfBuzz emits RTL
    glyphs in visual order (descending clusters), so every Arabic glyph
    would map to "" and the run would not extract or search. Reshape the
    run and remap each single-glyph cluster to its logical span instead.
    """

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
            gids = [g for g in gids_by_cluster[start] if g != 0]
            if gids:
                # A multi-glyph cluster maps its text to the first glyph only:
                # mapping it to every glyph would duplicate it on extraction.
                cid, _ = self._get_cid_and_width(gids[0])
                self._cid_to_unicode[cid] = txt[start:end]


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
