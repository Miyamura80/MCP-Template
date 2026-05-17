"""Pydantic input/output schemas for the Gmail integration.

Every input model carries ``user_id`` explicitly - the MCP tool factory
and FastAPI dependencies inject it from the authenticated principal in
a later wiring step (we deliberately do not use ContextVars).
"""

from datetime import datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Connect / status / disconnect
# ---------------------------------------------------------------------------


class GmailConnectInput(BaseModel):
    user_id: str = Field(..., description="The MCP user id starting the flow")


class GmailConnectResult(BaseModel):
    auth_url: str
    state: str


class GmailStatusInput(BaseModel):
    user_id: str


class GmailStatusResult(BaseModel):
    connected: bool
    email: str | None = None
    scopes: list[str] = Field(default_factory=list)
    granted_at: datetime | None = None


class GmailDisconnectInput(BaseModel):
    user_id: str


class GmailDisconnectResult(BaseModel):
    revoked: bool


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------


class GmailListDraftsInput(BaseModel):
    user_id: str
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
    user_id: str
    draft_id: str


class GmailDraft(BaseModel):
    draft_id: str
    to: str | None = None
    cc: str | None = None
    bcc: str | None = None
    subject: str | None = None
    body: str | None = None
    thread_id: str | None = None


class GmailUpdateDraftInput(BaseModel):
    user_id: str
    draft_id: str
    to: str | None = None
    subject: str | None = None
    body: str | None = None
    cc: str | None = None
    bcc: str | None = None


class GmailComposeInput(BaseModel):
    user_id: str
    to: str
    subject: str
    body: str
    cc: str | None = None
    bcc: str | None = None
    in_reply_to_thread_id: str | None = None


class GmailSendInput(BaseModel):
    user_id: str
    draft_id: str


class GmailSendResult(BaseModel):
    message_id: str
    thread_id: str | None = None
    sent_at: datetime


class GmailDiscardDraftInput(BaseModel):
    user_id: str
    draft_id: str


class GmailDiscardDraftResult(BaseModel):
    discarded: bool


# ---------------------------------------------------------------------------
# Inbox / threads
# ---------------------------------------------------------------------------


class GmailListInboxInput(BaseModel):
    user_id: str
    query: str | None = None
    limit: int = Field(default=25, ge=1, le=500)


class GmailMessageSummary(BaseModel):
    message_id: str
    thread_id: str | None = None
    subject: str | None = None
    from_: str | None = Field(default=None, alias="from")
    snippet: str | None = None
    date: datetime | None = None

    model_config = {"populate_by_name": True}


class GmailListInboxResult(BaseModel):
    messages: list[GmailMessageSummary]


class GmailGetThreadInput(BaseModel):
    user_id: str
    thread_id: str


class GmailAttachment(BaseModel):
    filename: str | None = None
    mime_type: str | None = None
    size: int | None = None
    attachment_id: str | None = None


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

    model_config = {"populate_by_name": True}


class GmailThread(BaseModel):
    thread_id: str
    messages: list[GmailThreadMessage]


# ---------------------------------------------------------------------------
# Curated inbox (deterministic heuristics for v1; DSPY ranker comes later)
# ---------------------------------------------------------------------------


class GmailCurateInboxInput(BaseModel):
    user_id: str
    query: str | None = None
    limit: int = Field(default=10, ge=1, le=100)


class GmailCuratedThread(BaseModel):
    thread_id: str
    subject: str | None = None
    from_: str | None = Field(default=None, alias="from")
    snippet: str | None = None
    last_message_at: datetime | None = None
    importance_score: float
    reasons: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class GmailCurateInboxResult(BaseModel):
    threads: list[GmailCuratedThread]
