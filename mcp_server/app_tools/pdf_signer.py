"""App-only tools used by the pdf_signer MCP App - the signing ceremony.

These are visible to the iframe via ``visibility=["app"]`` and are the ONLY
entry point that produces a signature (plus the elicitation fallback in
``mcp_server/enhancers/pdf_forms.py``, which runs the same server-side path).
The visibility hint is a convention some hosts do not enforce (see
MCP_UI_EDGE_CASES.md A4), so signing does not rely on it - the layered
guarantees are:

1. the server state machine: ``pdf_signer.sign`` refuses any document that
   is not ``awaiting_signature``, a state only ``pdf_request_signature``
   enters;
2. when the client supports elicitation, a host-native confirmation dialog
   that names the document and the typed name - rendered by the host outside
   the iframe, so neither the model nor a spoofed iframe can answer it;
3. the visibility hint itself.

Part of the PDF core (isolation seam): no Gmail imports.
"""

from __future__ import annotations

import base64

from mcp.server.fastmcp.server import Context
from mcp.types import ClientCapabilities, ElicitationCapability
from pydantic import BaseModel, Field

from mcp_server.app_tools._auth_guard import guard_user_id
from mcp_server.server import mcp
from models.pdf_forms import (
    PdfSignerCancelResult,
    PdfSignerDocument,
    PdfSignResult,
    SignaturePlacement,
)
from services.pdf_documents_repo import load_document
from services.pdf_signing import (
    PdfSigningInputError,
    PdfSigningStateError,
    abort_signing,
    perform_signing,
)

_APP_META = {"ui": {"visibility": ["app"]}}


@mcp.tool(
    name="pdf_signer.get_document",
    description="Fetch a PDF document session's bytes for the signing app viewer.",
    meta=_APP_META,
)
def get_document(doc_id: str, user_id: str = "") -> PdfSignerDocument:
    uid = guard_user_id(user_id)
    doc = load_document(doc_id, uid)
    placement = (
        SignaturePlacement.model_validate(doc.placement)
        if doc.placement is not None
        else None
    )
    return PdfSignerDocument(
        doc_id=doc.doc_id,
        filename=doc.filename,
        status=doc.status,  # ty: ignore[invalid-argument-type]
        page_count=doc.page_count,
        placement=placement,
        data_base64=base64.b64encode(doc.current_bytes).decode("ascii"),
    )


class _SignConfirmation(BaseModel):
    """Host-native confirmation schema (flat primitives only, per spec)."""

    confirm: bool = Field(description="Sign the document now")


@mcp.tool(
    name="pdf_signer.sign",
    description=(
        "Complete the signing ceremony (called by the signing app after the "
        "user typed their full legal name and ticked the consent box)."
    ),
    meta=_APP_META,
)
async def sign(
    ctx: Context,
    doc_id: str,
    typed_name: str,
    consent: bool = False,
    user_id: str = "",
) -> PdfSignResult:
    uid = guard_user_id(user_id)
    # Enforcement order per US-007: state first, ceremony inputs second,
    # host-native confirmation third, and only then the signature itself.
    doc = load_document(doc_id, uid)
    if doc.status != "awaiting_signature":
        raise PdfSigningStateError(doc_id=doc_id, status=doc.status)
    name = typed_name.strip()
    if not name or not consent:
        raise PdfSigningInputError(
            "Signing requires the full legal name typed by the user and the "
            "consent checkbox ticked."
        )
    confirmed = False
    if ctx.session.check_client_capability(
        ClientCapabilities(elicitation=ElicitationCapability())
    ):
        result = await ctx.elicit(
            message=(
                f"Sign '{doc.filename}' as \"{name}\"? This applies your "
                "electronic signature and seals the document - it cannot be "
                "edited afterwards."
            ),
            schema=_SignConfirmation,
        )
        if (
            result.action != "accept"
            or not getattr(result, "data", None)
            or not result.data.confirm
        ):
            # Stays awaiting_signature: the user may retry from the app.
            abort_signing(
                doc_id=doc_id,
                user_id=uid,
                reason="host_confirmation_declined",
                channel="app",
                back_to_open=False,
            )
            return PdfSignResult(
                doc_id=doc_id,
                status="declined",
                message=(
                    "Signing was not confirmed in the host dialog. The "
                    "document is still awaiting signature."
                ),
            )
        confirmed = True
    signed_doc, audit = perform_signing(
        doc_id=doc_id,
        user_id=uid,
        typed_name=name,
        consent=consent,
        channel="app",
        confirmed_via_elicitation=confirmed,
    )
    return PdfSignResult(
        doc_id=signed_doc.doc_id,
        status="signed",
        signed_by=audit["typed_name"],
        signed_at_utc=audit["signed_at_utc"],
        message=f"Signed by {audit['typed_name']} on {audit['signed_at_utc']}.",
    )


@mcp.tool(
    name="pdf_signer.cancel",
    description=(
        "Cancel the signing ceremony (called by the signing app); the "
        "document returns to 'open' and can be edited again."
    ),
    meta=_APP_META,
)
def cancel(doc_id: str, user_id: str = "") -> PdfSignerCancelResult:
    uid = guard_user_id(user_id)
    abort_signing(
        doc_id=doc_id,
        user_id=uid,
        reason="user_cancelled_in_app",
        channel="app",
        back_to_open=True,
    )
    return PdfSignerCancelResult(doc_id=doc_id, status="open")
