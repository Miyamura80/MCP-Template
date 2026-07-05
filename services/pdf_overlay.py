"""Canonical pypdf text-overlay machinery, shared by edit and signing.

Part of the PDF core (isolation seam): no Gmail imports.

Both ``pdf_edit``'s add_text ops and the signature stamp draw text by merging
a synthesized overlay page into the target page's content stream -
content-stream text renders identically in every viewer/rasterizer and
survives flattening, unlike annotation appearances. This module is the ONLY
place that builds those overlays, and deliberately the only PDF-core module
touching pypdf's private writer API (``_add_object``): a pypdf upgrade that
breaks it breaks exactly one file.
"""

from __future__ import annotations

import io

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def escape_pdf_text(text: str) -> bytes:
    """Escape a string for a PDF literal-string operand.

    Standard-14 fonts cover Latin-1; anything outside degrades to '?'.
    """
    escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return escaped.encode("latin-1", errors="replace")


def type1_font(writer: PdfWriter, base_font: str):
    """Register a standard-14 Type1 font on the writer; returns the ref."""
    return writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject(base_font),
            }
        )
    )


def build_text_overlay_page(
    width: float,
    height: float,
    fonts: dict[str, str],
    content: bytes,
):
    """Build a single page drawing ``content`` with the given font resources.

    ``fonts`` maps resource names to standard-14 base fonts, e.g.
    ``{"/F1": "/Helvetica"}``; ``content`` is a raw content stream referencing
    those names. The returned page is merge_page()-ready.
    """
    writer = PdfWriter()
    page = writer.add_blank_page(width=width, height=height)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject(resource): type1_font(writer, base)
                    for resource, base in fonts.items()
                }
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(content)
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return PdfReader(io.BytesIO(buffer.getvalue())).pages[0]
