"""Canned mailbox data for the no-auth demo mount.

Python port of ``mcp_server/dev_preview/src/fixtures.ts`` - the same fictional
inbox the MCP-UI dev preview renders, expressed as real service output models
so the demo tools' wire format (structuredContent, outputSchema) is identical
to production. When a tool contract changes, update both files.

Every address and company here is fictional; the dataset is deliberately
recognizable as a demo (see the account owner ``you@startup.com``).
"""

from datetime import UTC, datetime

from models.gmail import (
    GmailAttachment,
    GmailCuratedThread,
    GmailCurateInboxResult,
    GmailDraft,
    GmailLabelChip,
    GmailThread,
    GmailThreadMessage,
)

DEMO_USER = "demo"
DEMO_OWNER_ADDRESS = "you@startup.com"

# A minimal but valid single-page PDF, standing in for the term-sheet
# attachment so the inbox reader's download/preview path works in the demo.
DEMO_PDF_BASE64 = (
    "JVBERi0xLjEKMSAwIG9iajw8L1R5cGUvQ2F0YWxvZy9QYWdlcyAyIDAgUj4+ZW5kb2JqCjIg"
    "MCBvYmo8PC9UeXBlL1BhZ2VzL0tpZHNbMyAwIFJdL0NvdW50IDE+PmVuZG9iagozIDAgb2Jq"
    "PDwvVHlwZS9QYWdlL1BhcmVudCAyIDAgUi9NZWRpYUJveFswIDAgMjAwIDIwMF0+PmVuZG9i"
    "agp0cmFpbGVyPDwvUm9vdCAxIDAgUj4+"
)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def _curated(sender: str, **kw) -> GmailCuratedThread:
    """Build a curated thread.

    ``from`` is an alias-only field (Python keyword), so construction goes
    through ``model_validate`` - the same pattern as the curate service.
    """
    return GmailCuratedThread.model_validate({"from": sender, **kw})


def _message(sender: str, **kw) -> GmailThreadMessage:
    """Build a thread message with the aliased ``from`` field."""
    return GmailThreadMessage.model_validate({"from": sender, **kw})


_UNREAD = GmailLabelChip(name="Unread", bg_color="#e8f0fe", text_color="#1a73e8")

CURATED_THREADS: list[GmailCuratedThread] = [
    _curated(
        "Dana Whitfield <dana@northwind.vc>",
        thread_id="t-1001",
        subject="Series A term sheet - final redlines",
        snippet=(
            "Great call today. Attaching the final redlines - one open point on "
            "the liquidation preference…"
        ),
        last_message_at=_dt("2026-07-05T09:14:00"),
        importance_score=0.97,
        reasons=["Unread", "VIP sender", "Awaiting reply"],
        labels=[
            _UNREAD,
            GmailLabelChip(name="Finance", bg_color="#e6f4ea", text_color="#137333"),
        ],
        has_draft=False,
    ),
    _curated(
        "Priya Nair <priya@peoplehq.io>",
        thread_id="t-1002",
        subject="Re: Onsite interview loop for Staff Eng",
        snippet=(
            "Confirming Thursday 10am-2pm. I've looped in the panel. Let me know "
            "if the schedule works…"
        ),
        last_message_at=_dt("2026-07-05T07:41:00"),
        importance_score=0.82,
        reasons=["Unread", "Calendar"],
        labels=[_UNREAD],
        has_draft=True,
        draft_id="d-9001",
    ),
    _curated(
        "billing@vercel.com",
        thread_id="t-1003",
        subject="Your invoice #INV-2043 is ready",
        snippet="Your monthly invoice is now available. Amount due: $240.00…",
        last_message_at=_dt("2026-07-04T22:03:00"),
        importance_score=0.44,
        reasons=["Receipt"],
        labels=[
            GmailLabelChip(name="Receipts", bg_color="#fef7e0", text_color="#b06000")
        ],
        has_draft=False,
    ),
]

# Seed draft on t-1002 - also the draft the composer app opens by default.
SEED_DRAFT = GmailDraft(
    draft_id="d-9001",
    to="priya@peoplehq.io",
    cc="",
    bcc="",
    subject="Re: Onsite interview loop for Staff Eng",
    body=(
        "Hi Priya,\n\nThursday 10am-2pm works great - please send the calendar "
        "holds and I'll confirm with the panel on my side.\n\nLooking forward "
        "to it.\n\nBest,\nAlex"
    ),
    thread_id="t-1002",
    attachments=[],
)

THREADS: dict[str, GmailThread] = {
    "t-1001": GmailThread(
        thread_id="t-1001",
        messages=[
            _message(
                "Dana Whitfield <dana@northwind.vc>",
                message_id="m-1",
                to=DEMO_OWNER_ADDRESS,
                date=_dt("2026-07-05T09:14:00"),
                subject="Series A term sheet - final redlines",
                body_html=(
                    "<p>Hi,</p><p>Great call today. Attaching the final redlines"
                    " - one open point on the <b>liquidation preference</b> "
                    "(we're proposing 1x non-participating).</p><p>If that "
                    "works, we can sign this week.</p><p>Best,<br/>Dana</p>"
                ),
                attachments=[
                    GmailAttachment(
                        filename="termsheet-v7.pdf",
                        mime_type="application/pdf",
                        size=184320,
                        attachment_id="att-termsheet",
                    )
                ],
            )
        ],
    ),
    "t-1002": GmailThread(
        thread_id="t-1002",
        messages=[
            _message(
                "Priya Nair <priya@peoplehq.io>",
                message_id="m-2",
                to=DEMO_OWNER_ADDRESS,
                date=_dt("2026-07-05T07:41:00"),
                subject="Re: Onsite interview loop for Staff Eng",
                body_text=(
                    "Confirming Thursday 10am-2pm. I've looped in the panel. "
                    "Let me know if the schedule works and I'll send calendar "
                    "holds.\n\nThanks,\nPriya"
                ),
                attachments=[],
            )
        ],
        draft=SEED_DRAFT,
    ),
    "t-1003": GmailThread(
        thread_id="t-1003",
        messages=[
            _message(
                "billing@vercel.com",
                message_id="m-3",
                to=DEMO_OWNER_ADDRESS,
                date=_dt("2026-07-04T22:03:00"),
                subject="Your invoice #INV-2043 is ready",
                body_text=(
                    "Your monthly invoice is now available. Amount due: "
                    "$240.00. No action needed - auto-pay is on."
                ),
                attachments=[],
            )
        ],
    ),
}


def curated_inbox(limit: int = 10) -> GmailCurateInboxResult:
    """The canned ranked inbox, truncated to ``limit``."""
    return GmailCurateInboxResult(threads=CURATED_THREADS[:limit])


def get_thread(thread_id: str) -> GmailThread:
    """Look up a fixture thread; raise a demo-friendly error for unknown ids."""
    thread = THREADS.get(thread_id)
    if thread is None:
        known = ", ".join(sorted(THREADS))
        raise ValueError(
            f"Unknown demo thread {thread_id!r}. This demo mailbox has three "
            f"threads: {known}. Call gmail_curate_inbox to list them."
        )
    return thread.model_copy(deep=True)
