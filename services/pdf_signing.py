"""Server-side signing: visible stamp + embedded audit trail + PAdES seal.

Part of the PDF core (isolation seam): no Gmail imports.

This module is the ONLY place a signature is produced, and it is not a
``@service`` - it is called exclusively from the app-only ``pdf_signer.sign``
tool and the host-elicitation fallback, both human-gated. The state machine
is the hard guarantee: :func:`perform_signing` refuses any document that is
not ``awaiting_signature``, a state only ``pdf_request_signature`` can enter
and only a human ceremony can leave.

Signing steps (US-007):
1. visible stamp at the recorded placement - typed name in an italic face
   plus printed name / UTC date / pre-signature SHA-256 prefix;
2. audit trail embedded in the PDF's Info dictionary and appended to the
   session's ``audit`` column (typed name, timestamp, hash, consent, channel,
   elicitation-confirmed flag);
3. PAdES B-B seal with the server-held certificate via pyHanko. In dev, a
   self-signed certificate is generated once and cached under
   ``pdf_forms.signing.dev_cert_dir``; production should configure
   ``cert_path``/``key_path`` with a real key pair.

The stamp uses the standard-14 Helvetica-Oblique face rather than a bundled
script font: zero licensing surface and it renders identically everywhere
(the PRD's open question, resolved to the simplest v1).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from common import global_config
from common.global_config import root_dir
from db.models.pdf_documents import PdfDocument
from services.pdf_documents_repo import (
    PDF_STATUS_AWAITING_SIGNATURE,
    PDF_STATUS_OPEN,
    load_document,
    update_document,
)
from services.pdf_inspect import inspect_pdf

_SEAL_FIELD_NAME = "MyMCP-Seal"
_AUDIT_INFO_KEY = "/MyMCPSignatureAudit"

_NAME_FONT_SIZE = 14.0
_META_FONT_SIZE = 6.5


class PdfSigningStateError(Exception):
    """Signing attempted on a document that is not awaiting a signature."""

    def __init__(self, *, doc_id: str, status: str) -> None:
        self.status = status
        super().__init__(
            f"Document {doc_id!r} is {status!r}, not 'awaiting_signature'. "
            "Signing is only possible after pdf_request_signature."
        )


class PdfSigningInputError(Exception):
    """Missing typed name or consent - the ceremony inputs are mandatory."""


def _dev_cert_paths() -> tuple[Path, Path]:
    cert_dir = Path(global_config.pdf_forms.signing.dev_cert_dir)
    if not cert_dir.is_absolute():
        cert_dir = root_dir / cert_dir
    return cert_dir / "dev_cert.pem", cert_dir / "dev_key.pem"


def _generate_dev_cert(cert_path: Path, key_path: Path) -> None:
    """Create a self-signed dev sealing certificate (once, cached on disk)."""
    from cryptography import x509  # noqa: PLC0415 - dev-only path, keep import local
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: PLC0415
    from cryptography.x509.oid import NameOID  # noqa: PLC0415

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [
            x509.NameAttribute(
                NameOID.COMMON_NAME, "MyMCP Dev PDF Seal (self-signed, untrusted)"
            )
        ]
    )
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365 * 3))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,  # nonRepudiation - required for seals
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def sealing_cert_paths() -> tuple[Path, Path]:
    """Resolve (cert, key) PEM paths, generating the dev pair if needed."""
    cfg = global_config.pdf_forms.signing
    if cfg.cert_path and cfg.key_path:
        return Path(cfg.cert_path), Path(cfg.key_path)
    cert_path, key_path = _dev_cert_paths()
    if not (cert_path.exists() and key_path.exists()):
        _generate_dev_cert(cert_path, key_path)
    return cert_path, key_path


def _escape_pdf_text(text: str) -> bytes:
    escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return escaped.encode("latin-1", errors="replace")


def resolve_stamp_anchor(
    data: bytes, placement: dict[str, Any] | None
) -> tuple[int, float, float]:
    """Turn the recorded placement into (1-based page, x, y baseline)."""
    placement = placement or {}
    field_name = placement.get("field_name")
    if field_name is not None:
        inspection = inspect_pdf(data, include_text_layout=False)
        field = next((f for f in inspection.fields if f.name == field_name), None)
        if field is not None and field.rect is not None:
            return field.page or 1, field.rect[0] + 2.0, field.rect[1] + 4.0
    page = int(placement.get("page") or 1)
    x = float(placement.get("x") or 72.0)
    y = float(placement.get("y") or 72.0)
    return page, x, y


def _stamp_content(x: float, y: float, typed_name: str, meta_lines: list[str]) -> bytes:
    chunks = [
        b"BT /MyMCPSigF1 %.2f Tf %.2f %.2f Td (%s) Tj ET\n"
        % (_NAME_FONT_SIZE, x, y + 18.0, _escape_pdf_text(typed_name))
    ]
    chunks.extend(
        b"BT /MyMCPSigF2 %.2f Tf %.2f %.2f Td (%s) Tj ET\n"
        % (_META_FONT_SIZE, x, y + 10.0 - i * 8.0, _escape_pdf_text(line))
        for i, line in enumerate(meta_lines)
    )
    return b"".join(chunks)


def _font(writer: PdfWriter, base_font: str):
    return writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject(base_font),
            }
        )
    )


def _stamp_overlay_page(width: float, height: float, content: bytes):
    writer = PdfWriter()
    page = writer.add_blank_page(width=width, height=height)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/MyMCPSigF1"): _font(writer, "/Helvetica-Oblique"),
                    NameObject("/MyMCPSigF2"): _font(writer, "/Helvetica"),
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


def _apply_stamp_and_audit(
    data: bytes, placement: dict[str, Any] | None, audit: dict[str, Any]
) -> bytes:
    """One pypdf pass: draw the visible stamp and embed the audit in Info."""
    page_no, x, y = resolve_stamp_anchor(data, placement)
    signed_at = audit["signed_at_utc"]
    meta_lines = [
        f"Signed by {audit['typed_name']} on {signed_at}",
        f"SHA-256 (pre-signature): {audit['document_sha256'][:16]}...",
    ]
    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter(clone_from=reader)
    target = writer.pages[min(page_no, len(writer.pages)) - 1]
    overlay = _stamp_overlay_page(
        float(target.mediabox.width),
        float(target.mediabox.height),
        _stamp_content(x, y, audit["typed_name"], meta_lines),
    )
    target.merge_page(overlay)
    writer.add_metadata({_AUDIT_INFO_KEY: json.dumps(audit, sort_keys=True)})
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _apply_pades_seal(data: bytes) -> bytes:
    """Append the PAdES B-B platform seal as an incremental update."""

    def _seal() -> bytes:
        # Deliberate local imports: pyHanko pulls in a large dependency tree
        # that only this seal step needs; keep pdf_open/pdf_edit lean.
        from pyhanko.pdf_utils.incremental_writer import (  # noqa: PLC0415
            IncrementalPdfFileWriter,
        )
        from pyhanko.sign import fields, signers  # noqa: PLC0415

        cert_path, key_path = sealing_cert_paths()
        signer = signers.SimpleSigner.load(str(key_path), str(cert_path))
        if signer is None:
            raise RuntimeError(
                f"Failed to load the PDF sealing key pair from {cert_path} / "
                f"{key_path} (pdf_forms.signing config)."
            )
        writer = IncrementalPdfFileWriter(io.BytesIO(data))
        out = signers.sign_pdf(
            writer,
            signers.PdfSignatureMetadata(
                field_name=_SEAL_FIELD_NAME,
                subfilter=fields.SigSeedSubFilter.PADES,
                md_algorithm="sha256",
                reason="Electronic signature ceremony completed by the signer",
            ),
            signer=signer,
        )
        return out.getvalue()

    # pyHanko's sync sign_pdf calls asyncio.run() internally, which raises if
    # a loop is already running - and the signing tools ARE async (elicitation
    # needs the MCP session loop). Detect that case and seal on a worker
    # thread with its own fresh loop instead.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _seal()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_seal).result()


def perform_signing(
    *,
    doc_id: str,
    user_id: str,
    typed_name: str,
    consent: bool,
    channel: str,
    confirmed_via_elicitation: bool,
) -> tuple[PdfDocument, dict[str, Any]]:
    """Run the server-side half of the ceremony; returns (doc, audit record).

    The caller (app-only tool or elicitation fallback) is responsible for the
    human half: collecting the typed name + consent and, where the client
    supports it, the host-native confirmation.
    """
    doc = load_document(doc_id, user_id)
    if doc.status != PDF_STATUS_AWAITING_SIGNATURE:
        raise PdfSigningStateError(doc_id=doc_id, status=doc.status)
    name = typed_name.strip()
    if not name:
        raise PdfSigningInputError("typed_name must be the signer's full legal name.")
    if not consent:
        raise PdfSigningInputError(
            "consent must be true: the signer has to explicitly agree that "
            "typing their name constitutes their electronic signature."
        )

    audit: dict[str, Any] = {
        "typed_name": name,
        "signed_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "document_sha256": hashlib.sha256(doc.current_bytes).hexdigest(),
        "consent": True,
        "channel": channel,
        "confirmed_via_elicitation": confirmed_via_elicitation,
        "filename": doc.filename,
    }
    stamped = _apply_stamp_and_audit(doc.current_bytes, doc.placement, audit)
    sealed = _apply_pades_seal(stamped)
    updated = update_document(
        doc_id,
        user_id,
        data=sealed,
        new_status="signed",
        audit_event={"event": "signed", **audit},
    )
    return updated, audit


def abort_signing(
    *, doc_id: str, user_id: str, reason: str, channel: str, back_to_open: bool
) -> PdfDocument:
    """Record a failed/cancelled ceremony attempt in the audit trail.

    ``back_to_open=True`` is the user-cancel path (document editable again);
    ``False`` keeps it awaiting (e.g. a declined confirmation dialog - the
    user may retry from the signing UI).
    """
    return update_document(
        doc_id,
        user_id,
        new_status=PDF_STATUS_OPEN if back_to_open else None,
        audit_event={"event": "sign_aborted", "reason": reason, "channel": channel},
    )
