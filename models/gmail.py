"""Pydantic input/output schemas for the Gmail integration.

Every input model carries ``user_id`` explicitly - the MCP tool factory
and FastAPI dependencies inject it from the authenticated principal in
a later wiring step (we deliberately do not use ContextVars).
"""

from datetime import datetime

from pydantic import BaseModel, Field, computed_field

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
        """Alias of ``size`` (bytes) in the contract every transport returns."""
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

    ``attachments`` accepts a mix of new uploads (``AttachmentInput`` with
    base64 bytes) and references to files already on the draft
    (``AttachmentReference`` with just an ``attachment_id``), so a caller can
    edit the body repeatedly without re-uploading attachments. Omit
    ``attachments`` to keep every existing file; pass ``null`` (or ``[]``) to
    drop them all.
    """

    user_id: str = ""
    draft_id: str
    to: str | None = None
    subject: str | None = None
    body: str | None = None
    cc: str | None = None
    bcc: str | None = None
    attachments: list[AttachmentInput | AttachmentReference] | None = None


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
