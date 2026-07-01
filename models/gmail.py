"""Pydantic input/output schemas for the Gmail integration.

Every input model carries ``user_id`` explicitly - the MCP tool factory
and FastAPI dependencies inject it from the authenticated principal in
a later wiring step (we deliberately do not use ContextVars).
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, computed_field

# ---------------------------------------------------------------------------
# "Field omitted" vs "field set to null" sentinel
# ---------------------------------------------------------------------------


class _UnsetType:
    """Sentinel that survives the MCP transport to mean "field was omitted".

    A patch tool needs three distinct states per field: *omitted* (leave the
    stored value untouched), *null* (clear the stored value), and *a value*
    (overwrite). ``model_fields_set`` cannot supply this over MCP: FastMCP
    materializes every declared parameter to its default before invoking the
    tool (``func_metadata.model_dump_one_level`` calls ``getattr`` for every
    field), so an omitted field arrives as its default and lands in
    ``model_fields_set`` indistinguishably from one the caller passed. With a
    default of ``None`` that collapses omitted into null and silently clears
    fields the caller never mentioned.

    A dedicated sentinel default survives that round-trip: omitted -> ``UNSET``
    (preserve), ``null`` -> ``None`` (clear), value -> the value. Under
    ``arbitrary_types_allowed`` Pydantic validates it by identity (no coercion)
    and contributes no JSON schema for it, so the wire contract for these
    fields stays ``string | null`` - the sentinel never leaks into the tool's
    advertised input schema.
    """

    _instance: "_UnsetType | None" = None

    def __new__(cls) -> "_UnsetType":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = _UnsetType()


def unset_to[T](value: T | _UnsetType, fallback: T) -> T:
    """Resolve a patch field: ``fallback`` when omitted (``UNSET``), else ``value``.

    ``value`` may itself be ``None`` (an explicit "clear"), which is returned
    verbatim - only the ``UNSET`` sentinel selects the fallback.
    """
    if isinstance(value, _UnsetType):
        return fallback
    return value


# UNSET is not JSON-serializable on its own, so a model carrying it would raise
# in ``model_dump(mode="json")`` (e.g. if a patch model were ever logged or run
# through the idempotency store). Collapse the sentinel to ``null`` on the wire
# - an omitted field and an explicit-null field dump identically, which is
# correct: both mean "no value here". Validation still distinguishes them via
# the sentinel; only the serialized form collapses. ``when_used="json"`` keeps
# Python-mode dumps (``model_copy`` etc.) identity-preserving.
_UnsetJson = PlainSerializer(
    lambda v: None if isinstance(v, _UnsetType) else v, when_used="json"
)

# A patchable string field: string-or-null on the wire, UNSET-aware in Python.
# A plain alias (not a PEP 695 ``type`` statement) so Pydantic inlines the
# anyOf into each field instead of emitting a ``$ref`` to a named ``$def``.
_PatchStr = Annotated[str | None | _UnsetType, _UnsetJson]


# ---------------------------------------------------------------------------
# Connect / status / disconnect
# ---------------------------------------------------------------------------


class GmailConnectInput(BaseModel):
    user_id: str = Field(default="", description="The MCP user id starting the flow")


class GmailConnectResult(BaseModel):
    auth_url: str
    state: str


class GmailStatusInput(BaseModel):
    user_id: str = ""


class GmailStatusResult(BaseModel):
    connected: bool
    email: str | None = None
    scopes: list[str] = Field(default_factory=list)
    granted_at: datetime | None = None


class GmailDisconnectInput(BaseModel):
    user_id: str = ""


class GmailDisconnectResult(BaseModel):
    revoked: bool


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------


class AttachmentInput(BaseModel):
    """A file attachment to include on an outgoing email.

    Pass ``data_base64`` with the file content base64-encoded. Works from any
    MCP host (ChatGPT, Claude, etc.) regardless of filesystem access.
    """

    filename: str = Field(
        description="Display name for the attachment, e.g. 'report.pdf'",
        min_length=1,
        max_length=256,
    )
    mime_type: str = Field(
        description="MIME type, e.g. 'application/pdf'",
        min_length=1,
        max_length=256,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9!#$&\-^_.+]*\/[a-zA-Z0-9][a-zA-Z0-9!#$&\-^_.+]*$",
    )
    # Gmail's send limit is 25 MB; 34 MB base64 ≈ 25.5 MB decoded.
    data_base64: str = Field(
        description="Base64-encoded file content",
        min_length=1,
        max_length=34_000_000,
    )


class AttachmentUpload(BaseModel):
    """A normalized attachment payload ready to write into an outgoing MIME message.

    This is the single shape ``_build_raw_message`` consumes. It deliberately
    skips ``AttachmentInput``'s strict validators (mime pattern, size caps) so
    bytes re-downloaded from an existing draft - already accepted by Gmail once -
    never fail re-validation on a preserve path.
    """

    filename: str
    mime_type: str
    data_base64: str


class InlineImageUpload(BaseModel):
    """A CID-referenced inline image to re-emit when rebuilding an HTML body.

    HTML draft bodies reference inline images by ``cid:<content_id>``. On a
    whole-message rebuild these parts must be re-attached (as multipart/related
    with their ``Content-ID``) or the HTML renders with broken images.
    ``data_base64`` is base64url, matching ``AttachmentUpload``.
    """

    content_id: str
    mime_type: str
    data_base64: str


class AttachmentReference(BaseModel):
    """Reference to an attachment already present on a draft, by its stable id.

    Pass this (instead of an ``AttachmentInput``) in ``gmail_update_draft`` to
    keep an existing file on the draft without re-uploading its bytes. The
    ``attachment_id`` comes from the ``attachments[].attachment_id`` echoed by
    any prior draft mutation (compose / update / add / remove) or
    ``gmail_get_draft``.
    """

    attachment_id: str = Field(
        description="Stable id of an attachment already on the draft to preserve",
        min_length=1,
    )


class GmailListDraftsInput(BaseModel):
    user_id: str = ""
    limit: int = Field(default=20, ge=1, le=500)


class GmailDraftSummary(BaseModel):
    draft_id: str
    to: str | None = None
    subject: str | None = None
    snippet: str | None = None
    updated_at: datetime | None = None


class GmailListDraftsResult(BaseModel):
    drafts: list[GmailDraftSummary]


class GmailGetDraftInput(BaseModel):
    user_id: str = ""
    draft_id: str


class GmailDraftAttachment(BaseModel):
    """Metadata for an attachment already on a draft (read-only, no data blob).

    ``attachment_id`` is the stable handle for the file: pass it back as an
    ``AttachmentReference`` to ``gmail_update_draft`` (or to
    ``gmail_remove_attachment``) to preserve / remove the file without
    re-uploading its bytes.
    """

    filename: str | None = None
    mime_type: str | None = None
    size: int | None = None
    attachment_id: str | None = None
    message_id: str | None = None

    @computed_field
    @property
    def size_bytes(self) -> int | None:
        """Size in bytes - the name the public tool contract advertises.

        Emitted alongside ``size`` (not instead of it) because the committed
        composer UI bundle still reads ``size``; collapsing to a single key
        would require rebuilding that React bundle (``make build_apps``), which
        is out of scope for a pure service change.
        """
        return self.size


class GmailDraft(BaseModel):
    draft_id: str
    to: str | None = None
    cc: str | None = None
    bcc: str | None = None
    subject: str | None = None
    body: str | None = None
    thread_id: str | None = None
    attachments: list[GmailDraftAttachment] = Field(default_factory=list)

    @computed_field
    @property
    def body_preview(self) -> str | None:
        """First ~200 chars of the body, so callers can verify content cheaply."""
        if self.body is None:
            return None
        return self.body[:200]


class GmailUpdateDraftInput(BaseModel):
    """Patch input for ``gmail_update_draft``.

    Non-destructive by default: a field you omit is left unchanged on the
    draft; a field you set to ``null`` is cleared. This applies to ``to``,
    ``cc``, ``bcc``, ``subject``, ``body``, and ``attachments``.

    The omitted-vs-null distinction is carried by the ``UNSET`` sentinel
    default rather than ``model_fields_set``, because the latter cannot tell
    the two apart over the MCP transport (see ``_UnsetType``). Every patchable
    field therefore defaults to ``UNSET`` (preserve) instead of ``None``
    (clear).

    ``attachments`` accepts a mix of new uploads (``AttachmentInput`` with
    base64 bytes) and references to files already on the draft
    (``AttachmentReference`` with just an ``attachment_id``), so a caller can
    edit the body repeatedly without re-uploading attachments. Omit
    ``attachments`` to keep every existing file; pass ``null`` (or ``[]``) to
    drop them all.
    """

    # UNSET is not a Pydantic type, so allow it as a field default/value.
    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_id: str = ""
    draft_id: str
    to: _PatchStr = UNSET
    subject: _PatchStr = UNSET
    body: _PatchStr = UNSET
    cc: _PatchStr = UNSET
    bcc: _PatchStr = UNSET
    attachments: Annotated[
        list[AttachmentInput | AttachmentReference] | None | _UnsetType, _UnsetJson
    ] = UNSET


class GmailAddAttachmentInput(BaseModel):
    """Input for ``gmail_add_attachment``: append one file to a draft.

    Touches only the attachment list - body, subject, and recipients are
    preserved verbatim.
    """

    user_id: str = ""
    draft_id: str
    attachment: AttachmentInput


class GmailRemoveAttachmentInput(BaseModel):
    """Input for ``gmail_remove_attachment``: drop one file from a draft by id.

    Touches only the attachment list - body, subject, and recipients are
    preserved verbatim.
    """

    user_id: str = ""
    draft_id: str
    attachment_id: str = Field(
        description="Stable id of the attachment to remove", min_length=1
    )


class GmailDraftAttachmentsResult(BaseModel):
    """The draft's attachment list after an add/remove operation."""

    draft_id: str
    attachments: list[GmailDraftAttachment] = Field(default_factory=list)


class GmailComposeInput(BaseModel):
    user_id: str = ""
    to: str
    subject: str
    body: str
    cc: str | None = None
    bcc: str | None = None
    in_reply_to_thread_id: str | None = None
    attachments: list[AttachmentInput] = Field(default_factory=list)


class GmailSendInput(BaseModel):
    user_id: str = ""
    draft_id: str


class GmailSendResult(BaseModel):
    message_id: str
    thread_id: str | None = None
    sent_at: datetime


class GmailDiscardDraftInput(BaseModel):
    user_id: str = ""
    draft_id: str


class GmailDiscardDraftResult(BaseModel):
    discarded: bool


# ---------------------------------------------------------------------------
# Inbox / threads
# ---------------------------------------------------------------------------


class GmailListInboxInput(BaseModel):
    user_id: str = ""
    query: str | None = None
    limit: int = Field(default=25, ge=1, le=500)


class GmailMessageSummary(BaseModel):
    message_id: str
    thread_id: str | None = None
    subject: str | None = None
    from_: str | None = Field(default=None, alias="from")
    snippet: str | None = None
    date: datetime | None = None

    model_config = {"populate_by_name": True, "serialize_by_alias": True}


class GmailListInboxResult(BaseModel):
    messages: list[GmailMessageSummary]


class GmailGetThreadInput(BaseModel):
    user_id: str = ""
    thread_id: str


class GmailAttachment(BaseModel):
    filename: str | None = None
    mime_type: str | None = None
    size: int | None = None
    attachment_id: str | None = None
    content_id: str | None = None
    data: str | None = None


class GmailThreadMessage(BaseModel):
    message_id: str
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    cc: str | None = None
    date: datetime | None = None
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    attachments: list[GmailAttachment] = Field(default_factory=list)

    model_config = {"populate_by_name": True, "serialize_by_alias": True}


class GmailThread(BaseModel):
    thread_id: str
    messages: list[GmailThreadMessage]
    draft: GmailDraft | None = None


# ---------------------------------------------------------------------------
# Curated inbox (deterministic heuristics for v1; DSPY ranker comes later)
# ---------------------------------------------------------------------------


class GmailCurateInboxInput(BaseModel):
    user_id: str = ""
    query: str | None = None
    limit: int = Field(default=10, ge=1, le=100)


class GmailLabelChip(BaseModel):
    name: str
    bg_color: str = "#f1f3f4"
    text_color: str = "#444444"


class GmailCuratedThread(BaseModel):
    thread_id: str
    subject: str | None = None
    from_: str | None = Field(default=None, alias="from")
    snippet: str | None = None
    last_message_at: datetime | None = None
    importance_score: float
    reasons: list[str] = Field(default_factory=list)
    labels: list[GmailLabelChip] = Field(default_factory=list)
    has_draft: bool = False
    draft_id: str | None = None

    model_config = {"populate_by_name": True, "serialize_by_alias": True}


class GmailCurateInboxResult(BaseModel):
    threads: list[GmailCuratedThread]
