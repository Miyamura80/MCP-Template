"""Gmail inbox / threads / curate services.

All services here are headless: pure sync functions that take a Pydantic
input model and return a Pydantic output model. UI/enhancer affordances
live in ``mcp_server/enhancers`` and never touch this module.

Importance scoring for ``gmail_curate_inbox`` is deterministic for v1:

* +0.4 if the latest message carries the ``IMPORTANT`` label
* +0.3 if it carries the ``UNREAD`` label
* recency: ``0.3 * max(0, 1 - age_hours / 168)`` (linear decay over a week)

Upgrade path: swap the deterministic scorer for a DSPY signature that
ranks ``(subject, snippet, sender, age, labels)`` tuples; the function
shape stays the same so callers are unaffected.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger as log

from models.gmail import (
    GmailCuratedThread,
    GmailCurateInboxInput,
    GmailCurateInboxResult,
    GmailGetThreadInput,
    GmailListInboxInput,
    GmailListInboxResult,
    GmailMessageSummary,
    GmailThread,
    GmailThreadMessage,
)
from services import service
from services.gmail_svc import (
    _get_gmail_client,
    _headers_to_dict,
    _parse_message_resource,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _internal_date_to_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000.0, tz=UTC)
    except (TypeError, ValueError):
        return None


def _message_summary_from_metadata(msg: dict[str, Any]) -> GmailMessageSummary:
    headers = _headers_to_dict((msg.get("payload") or {}).get("headers"))
    return GmailMessageSummary.model_validate(
        {
            "message_id": msg.get("id") or "",
            "thread_id": msg.get("threadId"),
            "subject": headers.get("subject"),
            "from": headers.get("from"),
            "snippet": msg.get("snippet"),
            "date": _internal_date_to_dt(msg.get("internalDate")),
        }
    )


def _thread_message_from_parsed(parsed: dict[str, Any]) -> GmailThreadMessage:
    return GmailThreadMessage.model_validate(
        {
            "message_id": parsed.get("message_id") or "",
            "from": parsed.get("from"),
            "to": parsed.get("to"),
            "cc": parsed.get("cc"),
            "date": parsed.get("date"),
            "subject": parsed.get("subject"),
            "body_text": parsed.get("body_text"),
            "body_html": parsed.get("body_html"),
            "attachments": parsed.get("attachments") or [],
        }
    )


def _score_thread(
    *,
    label_ids: list[str],
    last_message_at: datetime | None,
    now: datetime,
) -> tuple[float, list[str]]:
    """Deterministic v1 importance score; see module docstring."""
    score = 0.0
    reasons: list[str] = []

    if "IMPORTANT" in label_ids:
        score += 0.4
        reasons.append("Marked IMPORTANT by Gmail")
    if "UNREAD" in label_ids:
        score += 0.3
        reasons.append("Unread")

    if last_message_at is not None:
        age_seconds = (now - last_message_at).total_seconds()
        age_hours = max(0.0, age_seconds / 3600.0)
        recency = 0.3 * max(0.0, 1.0 - age_hours / 168.0)
        if recency > 0:
            score += recency
            reasons.append(f"Recent (~{age_hours:.0f}h old)")

    return score, reasons


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


@service(
    name="gmail_list_inbox",
    description="List recent inbox messages, optionally filtered by a Gmail search query",
    input_model=GmailListInboxInput,
    output_model=GmailListInboxResult,
)
def gmail_list_inbox(input: GmailListInboxInput) -> GmailListInboxResult:
    svc = _get_gmail_client(input.user_id)
    q = input.query or "in:inbox"
    listing = (
        svc.users().messages().list(userId="me", q=q, maxResults=input.limit).execute()
    )
    summaries: list[GmailMessageSummary] = []
    for stub in listing.get("messages", []) or []:
        message_id = stub.get("id")
        if not message_id:
            continue
        meta = (
            svc.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"],
            )
            .execute()
        )
        summaries.append(_message_summary_from_metadata(meta))
    return GmailListInboxResult(messages=summaries)


@service(
    name="gmail_get_thread",
    description="Fetch a Gmail thread by id with full message bodies + attachments",
    input_model=GmailGetThreadInput,
    output_model=GmailThread,
)
def gmail_get_thread(input: GmailGetThreadInput) -> GmailThread:
    svc = _get_gmail_client(input.user_id)
    thread = (
        svc.users()
        .threads()
        .get(userId="me", id=input.thread_id, format="full")
        .execute()
    )
    messages: list[GmailThreadMessage] = []
    for m in thread.get("messages", []) or []:
        parsed = _parse_message_resource(m)
        messages.append(_thread_message_from_parsed(parsed))
    return GmailThread(thread_id=thread.get("id") or input.thread_id, messages=messages)


@service(
    name="gmail_curate_inbox",
    description="Rank recent inbox threads by a deterministic importance score (v1 heuristic; DSPY upgrade path documented in source)",
    input_model=GmailCurateInboxInput,
    output_model=GmailCurateInboxResult,
)
def gmail_curate_inbox(input: GmailCurateInboxInput) -> GmailCurateInboxResult:
    # Lazy import googleapiclient.errors so the module remains cheap when
    # the curate path is never exercised.
    from googleapiclient.errors import HttpError

    svc = _get_gmail_client(input.user_id)
    q = input.query or "in:inbox"
    over_fetch = max(input.limit * 3, 30)
    listing = (
        svc.users().threads().list(userId="me", q=q, maxResults=over_fetch).execute()
    )

    now = datetime.now(UTC)
    curated: list[GmailCuratedThread] = []

    for stub in listing.get("threads", []) or []:
        thread_id = stub.get("id")
        if not thread_id:
            continue

        try:
            thread = (
                svc.users()
                .threads()
                .get(
                    userId="me",
                    id=thread_id,
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
        except HttpError as exc:  # noqa: BLE001  # narrow: googleapiclient transport error
            # Defensive: a single thread's metadata fetch can fail (e.g. it
            # was deleted between list() and get()). Skip it but keep
            # curating the rest so the user still gets a useful result.
            log.warning("Skipping thread {} during curate: {}", thread_id, exc)
            continue

        messages = thread.get("messages") or []
        if not messages:
            continue
        last_msg = messages[-1]
        headers = _headers_to_dict((last_msg.get("payload") or {}).get("headers"))
        last_message_at = _internal_date_to_dt(last_msg.get("internalDate"))
        label_ids: list[str] = list(last_msg.get("labelIds") or [])

        score, reasons = _score_thread(
            label_ids=label_ids,
            last_message_at=last_message_at,
            now=now,
        )

        curated.append(
            GmailCuratedThread.model_validate(
                {
                    "thread_id": thread.get("id") or thread_id,
                    "subject": headers.get("subject"),
                    "from": headers.get("from"),
                    "snippet": last_msg.get("snippet"),
                    "last_message_at": last_message_at,
                    "importance_score": score,
                    "reasons": reasons,
                }
            )
        )

    curated.sort(key=lambda t: t.importance_score, reverse=True)
    return GmailCurateInboxResult(threads=curated[: input.limit])
