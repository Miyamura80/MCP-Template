"""Pydantic contracts for the PDF form-filling + user-gated signing tools.

Part of the PDF core (isolation seam): no imports from ``models.gmail``.
Sources and destinations are discriminated unions on ``type`` - v1 ships the
Gmail variants only, but new variants (upload, URL, filesystem) slot in
without touching the tools' signatures.

Coordinates are PDF user space: origin at the page's bottom-left corner,
units are points (1/72 inch). All tools share this convention.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Sources (pdf_open) and destinations (pdf_export)
# ---------------------------------------------------------------------------


class GmailAttachmentSource(BaseModel):
    """Locator for a PDF arriving as a Gmail attachment.

    Resolved server-side by the Gmail bridge - the bytes never travel
    through the model's context.
    """

    type: Literal["gmail_attachment"] = "gmail_attachment"
    message_id: str = Field(
        description="Id of the message the attachment lives on (from gmail_get_thread)",
        min_length=1,
    )
    attachment_id: str = Field(
        description="Stable attachment id from a gmail_get_thread attachment entry",
        min_length=1,
    )


# v1 has a single variant; becomes a discriminated union (Field
# discriminator="type") as soon as a second source type lands.
PdfSource = GmailAttachmentSource


class GmailDraftDestination(BaseModel):
    """Attach the exported PDF to an existing Gmail draft, server-side."""

    type: Literal["gmail_draft"] = "gmail_draft"
    draft_id: str = Field(min_length=1)
    filename: str | None = Field(
        default=None,
        description=(
            "Attachment filename. Defaults to '{original-stem}-signed.pdf' for "
            "signed documents, '{original-stem}-filled.pdf' otherwise."
        ),
    )


# v1 has a single variant; becomes a discriminated union alongside PdfSource.
PdfDestination = GmailDraftDestination


# ---------------------------------------------------------------------------
# Shared document shapes
# ---------------------------------------------------------------------------


class PdfFormField(BaseModel):
    """One AcroForm field, flattened for the LLM to reason about."""

    name: str = Field(description="Fully-qualified field name (use in set_field)")
    field_type: Literal["text", "checkbox", "radio", "choice", "signature", "unknown"]
    value: str | None = Field(default=None, description="Current value, if any")
    page: int | None = Field(
        default=None, description="1-based page the field's widget sits on"
    )
    rect: list[float] | None = Field(
        default=None,
        description="Widget rectangle [x0, y0, x1, y1] in PDF user space",
    )
    options: list[str] | None = Field(
        default=None,
        description=(
            "Allowed values: export options for choice fields, appearance "
            "states (e.g. ['/Off', '/Yes']) for checkboxes and radios"
        ),
    )
    required: bool = False
    read_only: bool = False


class PdfTextLine(BaseModel):
    """A line of existing text with its anchor position, for flat-PDF overlays."""

    page: int = Field(description="1-based page number")
    x: float = Field(description="Left edge of the line, PDF user space")
    y: float = Field(description="Baseline of the line, PDF user space")
    text: str


class PdfPageSize(BaseModel):
    page: int = Field(description="1-based page number")
    width: float
    height: float


class PdfPageImage(BaseModel):
    """A rasterized page (PNG) for visual verification of edits."""

    page: int = Field(description="1-based page number")
    mime_type: str = "image/png"
    data_base64: str


# ---------------------------------------------------------------------------
# pdf_open
# ---------------------------------------------------------------------------


class PdfOpenInput(BaseModel):
    user_id: str = ""
    source: PdfSource = Field(description="Where to fetch the PDF from")
    render_pages: list[int] = Field(
        default_factory=list,
        description=(
            "1-based page numbers to rasterize into the response as PNG images "
            "for visual inspection (capped by pdf_forms.render_max_pages)"
        ),
    )


class PdfOpenResult(BaseModel):
    doc_id: str = Field(description="Session handle for all subsequent pdf_* calls")
    filename: str
    status: Literal["open", "awaiting_signature", "signed"]
    page_count: int
    page_sizes: list[PdfPageSize] = Field(default_factory=list)
    has_acroform: bool = Field(
        description="True if the PDF has fillable form fields (use set_field); "
        "false means a flat PDF (use add_text overlays anchored on text_layout)"
    )
    fields: list[PdfFormField] = Field(default_factory=list)
    text_layout: list[PdfTextLine] = Field(
        default_factory=list,
        description="Existing text lines with coordinates (flat PDFs only), "
        "for anchoring add_text overlays",
    )
    text_layout_truncated: bool = Field(
        default=False,
        description="True if text_layout was cut at pdf_forms.text_layout_max_lines",
    )
    page_images: list[PdfPageImage] = Field(default_factory=list)
