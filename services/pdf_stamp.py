"""Signature stamp layout: placement anchor, footprint and text lines.

Part of the PDF core (isolation seam): no Gmail imports.

Split out of :mod:`services.pdf_signing` so the geometry the stamp is drawn
with and the footprint the signing app highlights come from one place.
Lines wider than the stamp shrink to fit (see :func:`stamp_lines`); a name
that would overflow even at its minimum size is refused by the ceremony via
:func:`name_fits_stamp`.
"""

from __future__ import annotations

from models.pdf_forms import SignaturePlacement
from services import ModelFacingError
from services.pdf_inspect import inspect_pdf
from services.pdf_overlay import OverlayText, fit_font_size

# Stamp geometry (PDF user space, points). The name baseline sits 18pt above
# the anchor, metadata lines below it; the footprint rect derived from these
# is what pdf_signer_get_document reports to the iframe, so the "Sign here"
# highlight and the actual stamp can never drift apart.
_NAME_FONT_SIZE = 14.0
_META_FONT_SIZE = 6.5
_NAME_BASELINE_OFFSET = 18.0
_META_BASELINE_OFFSET = 10.0
_META_LINE_SPACING = 8.0
_STAMP_WIDTH = 200.0
# Lines wider than the stamp shrink to fit, down to these floors; a name that
# still overflows at its floor is refused rather than spilling off the stamp.
_MIN_NAME_FONT_SIZE = 8.0
_MIN_META_FONT_SIZE = 4.0
_STAMP_DESCENT = 4.0  # below the anchor, covering the last metadata line
_STAMP_ASCENT = _NAME_BASELINE_OFFSET + _NAME_FONT_SIZE  # top of the typed name


class PdfPlacementResolutionError(ModelFacingError):
    """The recorded placement no longer resolves to a spot on the document.

    Raised instead of silently stamping a default position - a signature at
    the wrong place on a legal document is the worst possible fallback.
    """


def resolve_stamp_anchor(
    data: bytes, placement: SignaturePlacement
) -> tuple[int, float, float]:
    """Turn the recorded placement into (1-based page, x, y baseline).

    Raises :class:`PdfPlacementResolutionError` rather than guessing: the
    placement was validated by ``pdf_request_signature`` and the document is
    locked while awaiting, so failure to resolve means something is genuinely
    wrong and stamping a default spot would be worse than refusing.
    """
    if placement.field_name is not None:
        inspection = inspect_pdf(data, include_text_layout=False)
        field = next(
            (f for f in inspection.fields if f.name == placement.field_name), None
        )
        if field is None or field.rect is None:
            raise PdfPlacementResolutionError(
                f"Signature field {placement.field_name!r} no longer resolves "
                "to a widget rectangle in this document. Cancel and re-request "
                "the signature."
            )
        return field.page or 1, field.rect[0] + 2.0, field.rect[1] + 4.0
    if placement.page is None or placement.x is None or placement.y is None:
        raise PdfPlacementResolutionError(
            "Recorded placement is missing page/x/y coordinates. Cancel and "
            "re-request the signature."
        )
    return placement.page, placement.x, placement.y


def resolve_stamp_rect(
    data: bytes, placement: SignaturePlacement
) -> tuple[int, list[float]]:
    """The exact footprint the stamp will occupy: (page, [x0, y0, x1, y1]).

    Derived from the same anchor + geometry constants the stamp drawing uses,
    so the signing app's highlight box cannot drift from the real stamp.
    """
    page, x, y = resolve_stamp_anchor(data, placement)
    return page, [x, y - _STAMP_DESCENT, x + _STAMP_WIDTH, y + _STAMP_ASCENT]


def stamp_lines(
    x: float, y: float, typed_name: str, meta_lines: list[str]
) -> list[OverlayText]:
    """The stamp's text lines, each shrunk to fit the stamp width."""

    def fit(text: str, preferred: float, minimum: float) -> float:
        return fit_font_size(text, preferred, _STAMP_WIDTH, minimum) or minimum

    name_size = fit(typed_name, _NAME_FONT_SIZE, _MIN_NAME_FONT_SIZE)
    lines = [
        OverlayText(x, y + _NAME_BASELINE_OFFSET, name_size, typed_name, oblique=True)
    ]
    lines.extend(
        OverlayText(
            x,
            y + _META_BASELINE_OFFSET - i * _META_LINE_SPACING,
            fit(line, _META_FONT_SIZE, _MIN_META_FONT_SIZE),
            line,
        )
        for i, line in enumerate(meta_lines)
    )
    return lines


def name_fits_stamp(name: str) -> bool:
    """True when ``name`` fits the stamp width at or above its minimum size."""
    return (
        fit_font_size(name, _NAME_FONT_SIZE, _STAMP_WIDTH, _MIN_NAME_FONT_SIZE)
        is not None
    )
