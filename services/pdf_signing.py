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
   ``cert_path``/``key_path`` with a real key pair. Outside dev, falling back
   to the dev cert logs a loud error (:func:`warn_if_untrusted_seal`).

Latin-1 names stamp in standard-14 Helvetica-Oblique (nothing embedded).
Any other script is drawn with an embedded subset of the bundled Noto fonts
(SIL OFL 1.1, see ``services/pdf_fonts/OFL.txt``) with a synthetic oblique,
so a CJK, Greek, Cyrillic or Arabic name stamps exactly as typed. A name no
font covers is refused up front rather than stamped as empty boxes.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any

from loguru import logger as log
from pypdf import PdfReader, PdfWriter

from common import global_config
from common.global_config import root_dir
from db.models.pdf_documents import PdfDocument
from models.pdf_forms import PdfDocStatus, SignaturePlacement
from services import ModelFacingError
from services.pdf_documents_repo import load_document, update_document
from services.pdf_inspect import inspect_pdf
from services.pdf_overlay import (
    OverlayText,
    build_text_overlay_page,
    unrenderable_pdf_text,
)

_SEAL_FIELD_NAME = "MyMCP-Seal"
_AUDIT_INFO_KEY = "/MyMCPSignatureAudit"

# Stamp geometry (PDF user space, points). The name baseline sits 18pt above
# the anchor, metadata lines below it; the footprint rect derived from these
# is what pdf_signer.get_document reports to the iframe, so the "Sign here"
# highlight and the actual stamp can never drift apart.
_NAME_FONT_SIZE = 14.0
_META_FONT_SIZE = 6.5
_NAME_BASELINE_OFFSET = 18.0
_META_BASELINE_OFFSET = 10.0
_META_LINE_SPACING = 8.0
_STAMP_WIDTH = 200.0
_STAMP_DESCENT = 4.0  # below the anchor, covering the last metadata line
_STAMP_ASCENT = _NAME_BASELINE_OFFSET + _NAME_FONT_SIZE  # top of the typed name


class PdfSigningStateError(ModelFacingError):
    """Signing attempted on a document that is not awaiting a signature."""

    def __init__(self, *, doc_id: str, status: str) -> None:
        self.status = status
        super().__init__(
            f"Document {doc_id!r} is {status!r}, not 'awaiting_signature'. "
            "Signing is only possible after pdf_request_signature."
        )


class PdfSigningInputError(ModelFacingError):
    """Missing typed name or consent - the ceremony inputs are mandatory."""


class PdfPlacementResolutionError(ModelFacingError):
    """The recorded placement no longer resolves to a spot on the document.

    Raised instead of silently stamping a default position - a signature at
    the wrong place on a legal document is the worst possible fallback.
    """


def validate_ceremony(doc: PdfDocument, typed_name: str, consent: bool) -> str:
    """The security-critical ceremony predicate - the single implementation.

    Checks state, name, and consent, in that order; returns the stripped
    name. Every signing surface (app-only tool, elicitation fallback, the
    engine itself) must gate through this.
    """
    if doc.status != PdfDocStatus.AWAITING_SIGNATURE:
        raise PdfSigningStateError(doc_id=doc.doc_id, status=doc.status)
    name = typed_name.strip()
    if not name:
        _audit_rejection(doc, "empty_typed_name")
        raise PdfSigningInputError("typed_name must be the signer's full legal name.")
    if bad := unrenderable_pdf_text(name):
        # Control characters, or a script/ideograph no stamp font covers.
        # Stamping empty boxes into a signature on a legal document is
        # unacceptable, and a stamp that contradicts the audit trail is
        # worse than an honest error, so refuse.
        _audit_rejection(doc, "unrenderable_typed_name")
        raise PdfSigningInputError(
            f"typed_name contains characters the signature stamp cannot "
            f"render: {bad!r}. Control characters are never allowed; for "
            "other characters the stamp fonts cover Latin, Greek, Cyrillic, "
            "Arabic and common CJK/Hangul (operators can add fonts via "
            "pdf_forms.fonts.extra_font_paths)."
        )
    if not consent:
        _audit_rejection(doc, "consent_not_given")
        raise PdfSigningInputError(
            "consent must be true: the signer has to explicitly agree that "
            "typing their name constitutes their electronic signature."
        )
    return name


def _audit_rejection(doc: PdfDocument, reason: str) -> None:
    """Record a rejected ceremony submission (PRD: failed attempts audited).

    Status is untouched - the document stays ``awaiting_signature`` so the
    user can correct the input and retry. Conditional on the row still being
    ``awaiting_signature``: a rejection racing a successful seal must not
    append events after ``signed`` and muddy the sealed document's audit
    chronology.
    """
    update_document(
        doc.doc_id,
        doc.user_id,
        audit_event={"event": "sign_rejected", "reason": reason},
        audit_only_if_status=PdfDocStatus.AWAITING_SIGNATURE,
    )


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
    # Per-file atomic replace (temp + rename) prevents partial reads; the
    # exclusive lock in sealing_cert_paths() serializes whole-pair
    # generation, so concurrent processes can never interleave into a
    # mismatched key/cert pair (which would break every later seal load).
    pid_suffix = f".tmp-{os.getpid()}"
    key_tmp = key_path.with_name(key_path.name + pid_suffix)
    cert_tmp = cert_path.with_name(cert_path.name + pid_suffix)
    key_tmp.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_tmp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_tmp.replace(key_path)
    cert_tmp.replace(cert_path)


_UNTRUSTED_SEAL_WARNING = (
    "PDF SIGNING: pdf_forms.signing.cert_path and key_path are both unset "
    "outside development (DEV_ENV={dev_env}). Documents will be sealed with "
    "the auto-generated SELF-SIGNED dev certificate under {cert_dir}, and "
    "every real validator will report those seals as UNTRUSTED. Configure a "
    "real PEM key pair in pdf_forms.signing before signing production documents."
)
_untrusted_seal_warned = False


def _configured_seal_pair() -> tuple[Path, Path] | None:
    """The configured (cert, key) pair, or None when both are unset.

    A half-configured pair raises instead of returning None: falling back to
    the untrusted dev cert would hide the misconfiguration until a signed
    document fails validation. (The config model rejects this at load; this
    guards values changed after load.)
    """
    cfg = global_config.pdf_forms.signing
    if bool(cfg.cert_path) != bool(cfg.key_path):
        raise ValueError(
            "pdf_forms.signing: cert_path and key_path must be set together "
            "(or both left null for the dev self-signed cert); only "
            f"{'cert_path' if cfg.cert_path else 'key_path'} is set."
        )
    if cfg.cert_path and cfg.key_path:
        return Path(cfg.cert_path), Path(cfg.key_path)
    return None


def warn_if_untrusted_seal() -> None:
    """Log a loud error (once per process) if non-dev would seal with the dev cert.

    Called at server startup and again on the dev-cert fallback in
    :func:`sealing_cert_paths`, so stdio-only processes that never run the
    HTTP lifespan still surface it. Gated on ``global_config.is_dev``, which
    fails secure: ``prod``, ``staging``, unset or a typo all count as
    production. Raises ``ValueError`` on a half-configured pair.
    """
    global _untrusted_seal_warned  # noqa: PLW0603 - process-wide once flag
    if _configured_seal_pair() is not None or global_config.is_dev:
        return
    if _untrusted_seal_warned:
        return
    _untrusted_seal_warned = True
    log.error(
        _UNTRUSTED_SEAL_WARNING.format(
            dev_env=global_config.DEV_ENV or "<unset>",
            cert_dir=_dev_cert_paths()[0].parent,
        )
    )


def sealing_cert_paths() -> tuple[Path, Path]:
    """Resolve (cert, key) PEM paths, generating the dev pair if needed.

    First-time dev generation is serialized with an exclusive file lock
    (dev/POSIX-only path - production supplies cert_path/key_path): two
    processes generating concurrently could otherwise each replace one half
    of the pair, leaving a mismatched key/cert on disk. The winner generates;
    losers block on the lock, then see the finished pair on the recheck.
    Raises ``ValueError`` if only one of cert_path/key_path is set.
    """
    configured = _configured_seal_pair()
    if configured is not None:
        return configured
    warn_if_untrusted_seal()
    cert_path, key_path = _dev_cert_paths()
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path
    # POSIX-only import, deliberately below the production early-return so a
    # Windows deployment with configured certs never touches it.
    import fcntl  # noqa: PLC0415 - dev-cert generation only

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = cert_path.parent / ".dev_cert.lock"
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        # Recheck under the lock: a concurrent process may have generated
        # the pair while this one waited.
        if not (cert_path.exists() and key_path.exists()):
            _generate_dev_cert(cert_path, key_path)
    return cert_path, key_path


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


def _stamp_lines(
    x: float, y: float, typed_name: str, meta_lines: list[str]
) -> list[OverlayText]:
    lines = [
        OverlayText(
            x, y + _NAME_BASELINE_OFFSET, _NAME_FONT_SIZE, typed_name, oblique=True
        )
    ]
    lines.extend(
        OverlayText(
            x,
            y + _META_BASELINE_OFFSET - i * _META_LINE_SPACING,
            _META_FONT_SIZE,
            line,
        )
        for i, line in enumerate(meta_lines)
    )
    return lines


def _apply_stamp_and_audit(
    data: bytes, placement: SignaturePlacement, audit: dict[str, Any]
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
    overlay = build_text_overlay_page(
        float(target.mediabox.width),
        float(target.mediabox.height),
        _stamp_lines(x, y, audit["typed_name"], meta_lines),
        font_prefix="MyMCPSig",
    )
    target.merge_page(overlay)
    writer.add_metadata({_AUDIT_INFO_KEY: json.dumps(audit, sort_keys=True)})
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _apply_pades_seal(data: bytes, field_name: str) -> bytes:
    """Append the PAdES B-B platform seal as an incremental update.

    ``field_name`` is the signature field the seal lands in: the user's
    chosen AcroForm signature field for field-based placements (so the field
    they picked actually carries the signature), or ``_SEAL_FIELD_NAME`` -
    created fresh - for coordinate placements on flat PDFs.

    Synchronous and blocking (pyHanko's sync API owns its own event loop
    internally) - async callers must run :func:`perform_signing` via
    ``asyncio.to_thread``, never directly on the event loop.
    """
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
            field_name=field_name,
            subfilter=fields.SigSeedSubFilter.PADES,
            md_algorithm="sha256",
            reason="Electronic signature ceremony completed by the signer",
        ),
        signer=signer,
    )
    return out.getvalue()


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
    supports it, the host-native confirmation. Synchronous and CPU/IO-heavy
    (pypdf clone + RSA seal): async callers must wrap it in
    ``asyncio.to_thread``.

    Reloads the document by id deliberately, even though callers already
    hold a row: the host confirmation dialog can stay open indefinitely, and
    this reload (plus the atomic awaiting->signed transition in
    ``update_document``) guarantees the bytes that get sealed are the bytes
    that exist *after* the human confirmed.
    """
    doc = load_document(doc_id, user_id)
    name = validate_ceremony(doc, typed_name, consent)
    if doc.placement is None:
        raise PdfPlacementResolutionError(
            "Document has no recorded signature placement. Re-run "
            "pdf_request_signature."
        )
    placement = SignaturePlacement.model_validate(doc.placement)

    audit: dict[str, Any] = {
        "typed_name": name,
        "signed_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "document_sha256": hashlib.sha256(doc.current_bytes).hexdigest(),
        "consent": True,
        "channel": channel,
        "confirmed_via_elicitation": confirmed_via_elicitation,
        "filename": doc.filename,
    }
    stamped = _apply_stamp_and_audit(doc.current_bytes, placement, audit)
    sealed = _apply_pades_seal(stamped, placement.field_name or _SEAL_FIELD_NAME)
    updated = update_document(
        doc_id,
        user_id,
        data=sealed,
        new_status=PdfDocStatus.SIGNED,
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
        new_status=PdfDocStatus.OPEN if back_to_open else None,
        audit_event={"event": "sign_aborted", "reason": reason, "channel": channel},
    )
