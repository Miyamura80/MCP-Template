"""LLM-visible PDF tools: open, edit, request-signature, export.

Part of the PDF core (isolation seam): no Gmail imports. Sources and
destinations resolve through the ports in ``services/pdf_ports.py``; the
Gmail adapters live in ``services/pdf_gmail_bridge.py``.

Security invariant: no service in this module (or anywhere LLM-visible)
produces a signature. Signing happens only in the app-only
``pdf_signer.sign`` tool or the host-elicitation fallback, both of which are
human-gated (see the PRD's three-layer model).

Coordinates are PDF user space: origin bottom-left, points (1/72 inch).
"""

from __future__ import annotations

from models.pdf_forms import (
    PdfOpenInput,
    PdfOpenResult,
)
from services import service
from services.pdf_documents_repo import create_document
from services.pdf_inspect import inspect_pdf
from services.pdf_ports import resolve_source
from services.pdf_render import render_pages


@service(
    name="pdf_open",
    description=(
        "Open a PDF into a server-side editing session and return everything "
        "needed to plan edits: a doc_id handle, the fillable form-field "
        "inventory (name, type, current value, page, rect), and - for flat "
        "PDFs without form fields - the existing text layout with coordinates "
        "for anchoring text overlays. The PDF bytes stay server-side; every "
        "later pdf_* call takes the doc_id. Optionally pass render_pages "
        "(1-based page numbers) to also get PNG renders of pages for visual "
        "inspection. Coordinates are PDF user space: origin at the page's "
        "bottom-left corner, in points (1/72 inch)."
    ),
    input_model=PdfOpenInput,
    output_model=PdfOpenResult,
)
def pdf_open(input: PdfOpenInput) -> PdfOpenResult:
    resolved = resolve_source(input.user_id, input.source)
    inspection = inspect_pdf(resolved.data)
    doc = create_document(
        user_id=input.user_id,
        filename=resolved.filename,
        data=resolved.data,
        page_count=inspection.page_count,
        source_ref=input.source.model_dump(),
    )
    page_images = render_pages(resolved.data, input.render_pages, inspection.page_count)
    return PdfOpenResult(
        doc_id=doc.doc_id,
        filename=doc.filename,
        status="open",
        page_count=inspection.page_count,
        page_sizes=inspection.page_sizes,
        has_acroform=inspection.has_acroform,
        fields=inspection.fields,
        text_layout=inspection.text_layout,
        text_layout_truncated=inspection.text_layout_truncated,
        page_images=page_images,
    )
