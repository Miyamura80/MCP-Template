"""pypdf-based PDF inspection: field inventory, text layout, page geometry.

Part of the PDF core (isolation seam): no Gmail imports. Everything here is
bytes-in -> data-out with no session or transport awareness.

Coordinates are PDF user space (origin bottom-left, points) throughout.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from pypdf import PdfReader
from pypdf.generic import DictionaryObject

from common import global_config
from models.pdf_forms import PdfFormField, PdfPageSize, PdfTextLine

# Non-PDF magic numbers we can name in the "not a PDF" error message.
_KNOWN_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"PK\x03\x04", "application/zip (or an Office document)"),
    (b"{\\rtf", "application/rtf"),
)

# AcroForm field-flag bits (PDF 2.0 spec, table 226/229).
_FF_READ_ONLY = 1
_FF_REQUIRED = 1 << 1
_FF_RADIO = 1 << 15
_FF_PUSHBUTTON = 1 << 16

# Fragments whose baselines sit within this many points are the same line.
_LINE_Y_TOLERANCE = 2.0


class PdfNotAPdfError(Exception):
    """Raised when the fetched bytes are not a PDF."""

    def __init__(self, detected: str) -> None:
        self.detected = detected
        super().__init__(
            f"The file is not a PDF (detected: {detected}). "
            "pdf_open only accepts PDF documents."
        )


def ensure_pdf_magic(data: bytes) -> None:
    """Reject non-PDF bytes with the detected type named in the error."""
    if data.startswith(b"%PDF-"):
        return
    for magic, mime in _KNOWN_MAGIC:
        if data.startswith(magic):
            raise PdfNotAPdfError(mime)
    raise PdfNotAPdfError("application/octet-stream")


@dataclass
class PdfInspection:
    page_count: int
    page_sizes: list[PdfPageSize]
    has_acroform: bool
    fields: list[PdfFormField]
    text_layout: list[PdfTextLine] = field(default_factory=list)
    text_layout_truncated: bool = False


def inspect_pdf(data: bytes, *, include_text_layout: bool = True) -> PdfInspection:
    """Read a PDF's geometry, AcroForm inventory and (for flat PDFs) text layout."""
    ensure_pdf_magic(data)
    reader = PdfReader(io.BytesIO(data))
    page_sizes = [
        PdfPageSize(
            page=i + 1,
            width=float(p.mediabox.width),
            height=float(p.mediabox.height),
        )
        for i, p in enumerate(reader.pages)
    ]
    fields = _collect_fields(reader)
    inspection = PdfInspection(
        page_count=len(reader.pages),
        page_sizes=page_sizes,
        has_acroform=bool(fields),
        fields=fields,
    )
    # Layout is the overlay anchor for flat PDFs; AcroForm docs are filled by
    # field name, so skipping layout there keeps responses bounded.
    if include_text_layout and not inspection.has_acroform:
        inspection.text_layout, inspection.text_layout_truncated = _extract_layout(
            reader
        )
    return inspection


def _qualified_name(obj: DictionaryObject) -> str | None:
    """Build the fully-qualified field name by walking the /Parent chain."""
    parts: list[str] = []
    node: DictionaryObject | None = obj
    while node is not None:
        t = node.get("/T")
        if t is not None:
            parts.append(str(t))
        parent = node.get("/Parent")
        node = parent.get_object() if parent is not None else None
    if not parts:
        return None
    return ".".join(reversed(parts))


def _inherited(obj: DictionaryObject, key: str):
    """Read a field attribute, falling back up the /Parent chain."""
    node: DictionaryObject | None = obj
    while node is not None:
        if key in node:
            return node[key]
        parent = node.get("/Parent")
        node = parent.get_object() if parent is not None else None
    return None


def _field_type(obj: DictionaryObject) -> str:
    ft = _inherited(obj, "/FT")
    flags = int(_inherited(obj, "/Ff") or 0)
    if ft == "/Tx":
        return "text"
    if ft == "/Ch":
        return "choice"
    if ft == "/Sig":
        return "signature"
    if ft == "/Btn":
        if flags & _FF_PUSHBUTTON:
            return "unknown"  # pushbuttons hold no value
        return "radio" if flags & _FF_RADIO else "checkbox"
    return "unknown"


def _appearance_states(obj: DictionaryObject) -> list[str]:
    ap = obj.get("/AP")
    if ap is None:
        return []
    normal = ap.get_object().get("/N")
    if normal is None:
        return []
    return [str(k) for k in normal.get_object()]


def _choice_options(obj: DictionaryObject) -> list[str]:
    opts = _inherited(obj, "/Opt")
    if opts is None:
        return []
    result = []
    for o in opts.get_object():
        o = o.get_object()
        # /Opt entries are either a string or [export_value, display_value].
        result.append(str(o[0].get_object()) if isinstance(o, list) else str(o))
    return result


def _collect_fields(reader: PdfReader) -> list[PdfFormField]:
    """Walk widget annotations page-by-page, aggregating widgets per field name."""
    by_name: dict[str, PdfFormField] = {}
    for page_no, page in enumerate(reader.pages, start=1):
        for ref in page.get("/Annots") or []:
            obj = ref.get_object()
            if obj.get("/Subtype") != "/Widget":
                continue
            name = _qualified_name(obj)
            if name is None:
                continue
            ftype = _field_type(obj)
            states = _appearance_states(obj)
            existing = by_name.get(name)
            if existing is not None:
                # Extra widget of a radio group: union its appearance states.
                if existing.options is not None:
                    merged = [*existing.options]
                    merged.extend(s for s in states if s not in merged)
                    existing.options = merged
                continue
            flags = int(_inherited(obj, "/Ff") or 0)
            value = _inherited(obj, "/V")
            options: list[str] | None = None
            if ftype == "choice":
                options = _choice_options(obj) or None
            elif ftype in ("checkbox", "radio"):
                options = states or None
            rect = obj.get("/Rect")
            by_name[name] = PdfFormField(
                name=name,
                field_type=ftype,  # ty: ignore[invalid-argument-type]
                value=None if value is None else str(value),
                page=page_no,
                rect=None if rect is None else [float(v) for v in rect],
                options=options,
                required=bool(flags & _FF_REQUIRED),
                read_only=bool(flags & _FF_READ_ONLY),
            )
    return list(by_name.values())


def _extract_layout(reader: PdfReader) -> tuple[list[PdfTextLine], bool]:
    """Extract text fragments with positions and merge them into lines.

    Positions come from the text matrix composed with the transformation
    matrix pypdf hands the visitor, so simple translated/scaled text lands at
    its true user-space coordinates.
    """
    max_lines = global_config.pdf_forms.text_layout_max_lines
    lines: list[PdfTextLine] = []
    truncated = False
    for page_no, page in enumerate(reader.pages, start=1):
        fragments: list[tuple[float, float, str]] = []

        def _visit(text, cm, tm, font_dict, font_size, frags=fragments):
            content = text.strip()
            if not content:
                return
            tx, ty = tm[4], tm[5]
            x = cm[0] * tx + cm[2] * ty + cm[4]
            y = cm[1] * tx + cm[3] * ty + cm[5]
            frags.append((x, y, content))

        page.extract_text(visitor_text=_visit)
        for line in _merge_fragments(page_no, fragments):
            if len(lines) >= max_lines:
                truncated = True
                return lines, truncated
            lines.append(line)
    return lines, truncated


def _merge_fragments(
    page_no: int, fragments: list[tuple[float, float, str]]
) -> list[PdfTextLine]:
    """Join fragments sharing a baseline into reading-order lines."""
    ordered = sorted(fragments, key=lambda f: (-f[1], f[0]))
    merged: list[PdfTextLine] = []
    for x, y, content in ordered:
        last = merged[-1] if merged else None
        if last is not None and abs(last.y - y) <= _LINE_Y_TOLERANCE:
            last.text = f"{last.text} {content}"
            last.x = min(last.x, x)
        else:
            merged.append(PdfTextLine(page=page_no, x=x, y=y, text=content))
    return merged
