"""Gmail inbox / threads / curate services.

All services here are headless: pure sync functions that take a Pydantic
input model and return a Pydantic output model. UI/enhancer affordances
live in ``mcp_server/enhancers`` and never touch this module.

Importance scoring for ``gmail_curate_inbox`` is deterministic for v1:

* +1.0  if it carries ``Needs Reply`` (dominates recency)
* +0.3  if it carries the ``UNREAD`` label
* +0.3  if it carries ``Customer/Prospect``
* +0.25 if it carries ``To Do``
* +0.2  if it carries ``Travel``
* recency: ``0.3 * max(0, 1 - age_hours / 168)`` (linear decay over a week)

Note: ``FYI`` label is intentionally excluded - too noisy to be a useful signal.

Note: Gmail's auto-applied ``IMPORTANT`` label is intentionally excluded -
its classifier is too noisy to be a reliable signal.

Gmail category tabs ``Updates``, ``Promotions``, ``Social``, and ``Forums``
are excluded from the curate query - only Primary tab emails enter the
scoring pipeline. User-applied labels from the classification cronjob
(``Newsletter``, ``Promotion``, ``Marketing``, ``Notifications``,
``Product Updates``, ``Marketing/Webinar``, ``Webinar``, ``Cold Outbound``,
``NPS Survey``, ``Survey``) are also excluded.

Upgrade path: swap the deterministic scorer for a DSPY signature that
ranks ``(subject, snippet, sender, age, labels)`` tuples; the function
shape stays the same so callers are unaffected.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from loguru import logger as log
from pydantic import BaseModel

from models.gmail import (
    GmailCuratedThread,
    GmailCurateInboxInput,
    GmailCurateInboxResult,
    GmailDraft,
    GmailGetThreadInput,
    GmailLabelChip,
    GmailListInboxInput,
    GmailListInboxResult,
    GmailMessageSummary,
    GmailThread,
    GmailThreadMessage,
)
from services import service
from services.gmail_draft_helpers import _draft_resource_to_model
from services.gmail_svc import (
    _get_gmail_client,
    _headers_to_dict,
    _parse_message_resource,
)


# Inputs/outputs for thread-modify services kept inline (small,
# transport-agnostic) so models/gmail.py doesn't need to grow for these
# small toggles. Promote to models/gmail.py if reused elsewhere.
class GmailThreadModifyInput(BaseModel):
    user_id: str = ""
    thread_id: str


class GmailMarkReadResult(BaseModel):
    marked_read: bool


class GmailArchiveResult(BaseModel):
    archived: bool


class GmailMarkDoneResult(BaseModel):
    marked_done: bool
    label_id: str | None = None


class GmailUnmarkDoneResult(BaseModel):
    unmarked_done: bool


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


_CID_RE = re.compile(r'(?:src|background)\s*=\s*["\']cid:([^"\']+)["\']', re.IGNORECASE)


def _resolve_inline_images(svc: Any, message_id: str, parsed: dict[str, Any]) -> None:
    """Fetch missing image data and replace cid: references in HTML with data URIs."""
    attachments: list[dict[str, Any]] = parsed.get("attachments") or []
    cid_map: dict[str, str] = {}

    for att in attachments:
        mime = att.get("mime_type") or ""
        if not mime.startswith("image/"):
            continue
        cid = att.get("content_id")
        aid = att.get("attachment_id")

        if cid and not att.get("data") and aid:
            try:
                resp = (
                    svc.users()
                    .messages()
                    .attachments()
                    .get(userId="me", messageId=message_id, id=aid)
                    .execute()
                )
                raw = resp.get("data", "")
                att["data"] = raw.replace("-", "+").replace("_", "/")
                att["data"] += "=" * (-len(att["data"]) % 4)
            except Exception:  # noqa: BLE001  # best-effort image fetch
                continue

        if cid and att.get("data"):
            cid_map[cid] = f"data:{mime};base64,{att['data']}"

    html = parsed.get("body_html")
    if html and cid_map:

        def _replace_cid(match: re.Match[str]) -> str:
            attr = match.group(0).split("=")[0]
            cid_ref = match.group(1)
            data_uri = cid_map.get(cid_ref)
            if data_uri:
                return f'{attr}="{data_uri}"'
            return match.group(0)

        parsed["body_html"] = _CID_RE.sub(_replace_cid, html)


_BATCH_CHUNK_SIZE = 50  # Gmail batch API limit is 100; stay well under


def _batch_get_threads(
    svc: Any,
    thread_ids: list[str],
    *,
    fmt: str = "metadata",
    metadata_headers: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch multiple threads in a single batched HTTP request.

    Returns a dict mapping thread_id → thread payload. Threads that fail
    (deleted between list and get) are silently skipped.
    """
    results: dict[str, dict[str, Any]] = {}

    for offset in range(0, len(thread_ids), _BATCH_CHUNK_SIZE):
        chunk = thread_ids[offset : offset + _BATCH_CHUNK_SIZE]
        batch = svc.new_batch_http_request()
        for tid in chunk:
            kwargs: dict[str, Any] = {"userId": "me", "id": tid, "format": fmt}
            if metadata_headers:
                kwargs["metadataHeaders"] = metadata_headers
            req = svc.users().threads().get(**kwargs)

            def _cb(
                request_id: str, response: Any, exception: Any, _tid: str = tid
            ) -> None:
                if exception is not None:
                    log.warning("Batch thread fetch failed for {}: {}", _tid, exception)
                    return
                results[_tid] = response

            batch.add(req, callback=_cb)
        batch.execute()

    return results


def _batch_get_messages(
    svc: Any,
    message_ids: list[str],
    *,
    fmt: str = "metadata",
    metadata_headers: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch multiple messages in a single batched HTTP request."""
    results: dict[str, dict[str, Any]] = {}

    for offset in range(0, len(message_ids), _BATCH_CHUNK_SIZE):
        chunk = message_ids[offset : offset + _BATCH_CHUNK_SIZE]
        batch = svc.new_batch_http_request()
        for mid in chunk:
            kwargs: dict[str, Any] = {"userId": "me", "id": mid, "format": fmt}
            if metadata_headers:
                kwargs["metadataHeaders"] = metadata_headers
            req = svc.users().messages().get(**kwargs)

            def _cb(
                request_id: str, response: Any, exception: Any, _mid: str = mid
            ) -> None:
                if exception is not None:
                    log.warning(
                        "Batch message fetch failed for {}: {}", _mid, exception
                    )
                    return
                results[_mid] = response

            batch.add(req, callback=_cb)
        batch.execute()

    return results


_USER_LABEL_BOOSTS: dict[str, float] = {
    "Needs Reply": 1.0,
    "Customer/Prospect": 0.30,
    "To Do": 0.25,
    "Travel": 0.20,
}

_DISPLAY_ONLY_LABELS: set[str] = {
    "FYI",
    "Waiting",
    "Action Required",
    "High Priority",
    "Follow-up",
    "Needs Review",
    "KYC",
    "Fundraising",
}

_EXCLUDE_LABELS: set[str] = {
    "Newsletter",
    "Promotion",
    "Marketing",
    "Notifications",
    "Product Updates",
    "Marketing/Webinar",
    "Webinar",
    "Cold Outbound",
    "NPS Survey",
    "Survey",
}

_ALL_CHIP_LABELS: set[str] = set(_USER_LABEL_BOOSTS) | _DISPLAY_ONLY_LABELS

_ALL_TRACKED_LABELS: set[str] = _ALL_CHIP_LABELS | _EXCLUDE_LABELS

_SYSTEM_LABEL_COLORS: dict[str, tuple[str, str]] = {
    "UNREAD": ("#e8f0fe", "#1a73e8"),
}


def _format_recency(age_hours: float) -> str:
    if age_hours < 1:
        return "Just now"
    if age_hours < 24:
        return f"{int(age_hours)}h ago"
    days = age_hours / 24.0
    if days < 7:
        d = int(days)
        return f"{d} day{'s' if d != 1 else ''} ago"
    weeks = days / 7.0
    w = int(weeks)
    return f"{w} week{'s' if w != 1 else ''} ago"


def _score_thread(
    *,
    label_ids: list[str],
    label_names: set[str],
    label_colors: dict[str, tuple[str, str]],
    last_message_at: datetime | None,
    now: datetime,
) -> tuple[float, list[str], list[GmailLabelChip]]:
    """Deterministic v1 importance score; see module docstring."""
    score = 0.0
    reasons: list[str] = []
    chips: list[GmailLabelChip] = []

    if "UNREAD" in label_ids:
        score += 0.3
        bg, text = _SYSTEM_LABEL_COLORS["UNREAD"]
        chips.append(GmailLabelChip(name="Unread", bg_color=bg, text_color=text))

    for name, boost in _USER_LABEL_BOOSTS.items():
        if name in label_names:
            score += boost
            bg, text = label_colors.get(name, ("#f1f3f4", "#444444"))
            chips.append(GmailLabelChip(name=name, bg_color=bg, text_color=text))

    for name in _DISPLAY_ONLY_LABELS:
        if name in label_names:
            bg, text = label_colors.get(name, ("#f1f3f4", "#444444"))
            chips.append(GmailLabelChip(name=name, bg_color=bg, text_color=text))

    if last_message_at is not None:
        age_seconds = (now - last_message_at).total_seconds()
        age_hours = max(0.0, age_seconds / 3600.0)
        recency = 0.3 * max(0.0, 1.0 - age_hours / 168.0)
        if recency > 0:
            score += recency
            reasons.append(_format_recency(age_hours))

    return score, reasons, chips


_MCP_DONE_LABEL_NAME = "MCP/Done"


def _find_mcp_done_label(svc: Any) -> str | None:
    """Return the label ID for ``MCP/Done`` if it exists, else ``None``."""
    labels_response = svc.users().labels().list(userId="me").execute()
    for label in labels_response.get("labels", []):
        if label.get("name") == _MCP_DONE_LABEL_NAME:
            return label["id"]
    return None


def _get_or_create_mcp_done_label(svc: Any) -> str:
    """Return the label ID for ``MCP/Done``, creating it if absent."""
    # Deliberate deferral: the Google SDK is heavy - only load it when a Gmail
    # API call is actually made, not at service discovery / module import.
    from googleapiclient.errors import HttpError  # noqa: PLC0415

    existing = _find_mcp_done_label(svc)
    if existing is not None:
        return existing

    try:
        created = (
            svc.users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": _MCP_DONE_LABEL_NAME,
                    "labelListVisibility": "labelHide",
                    "messageListVisibility": "hide",
                },
            )
            .execute()
        )
        return created["id"]
    except HttpError as exc:
        if exc.resp.status == 409:
            existing = _find_mcp_done_label(svc)
            if existing is not None:
                return existing
        raise


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


@service(
    name="gmail_list_inbox",
    description="List recent inbox messages, optionally filtered by a Gmail search query. When the user asks to find or open a specific email, ALWAYS follow up by calling gmail_get_thread with the thread_id to render the full conversation in an interactive UI.",
    input_model=GmailListInboxInput,
    output_model=GmailListInboxResult,
)
def gmail_list_inbox(input: GmailListInboxInput) -> GmailListInboxResult:
    svc = _get_gmail_client(input.user_id)
    q = f"in:inbox ({input.query})" if input.query else "in:inbox"
    listing = (
        svc.users().messages().list(userId="me", q=q, maxResults=input.limit).execute()
    )
    message_ids = [
        stub["id"] for stub in (listing.get("messages", []) or []) if stub.get("id")
    ]
    if not message_ids:
        return GmailListInboxResult(messages=[])
    fetched = _batch_get_messages(
        svc,
        message_ids,
        metadata_headers=["From", "To", "Subject", "Date"],
    )
    summaries: list[GmailMessageSummary] = []
    for mid in message_ids:
        meta = fetched.get(mid)
        if meta:
            summaries.append(_message_summary_from_metadata(meta))
    return GmailListInboxResult(messages=summaries)


@service(
    name="gmail_get_thread",
    description="Fetch a Gmail thread by id with full message bodies + attachments. When an interactive UI is rendered alongside the result, keep your text response brief since the user can browse the conversation in the UI.",
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
    # Check for a draft on this thread (best-effort) before building
    # the messages list so we can exclude the draft's underlying message.
    draft: GmailDraft | None = None
    draft_message_id: str | None = None
    try:
        drafts_resp = svc.users().drafts().list(userId="me", maxResults=50).execute()
        for d in drafts_resp.get("drafts", []) or []:
            d_msg = d.get("message") or {}
            if d_msg.get("threadId") == input.thread_id:
                full_draft = (
                    svc.users()
                    .drafts()
                    .get(userId="me", id=d["id"], format="full")
                    .execute()
                )
                draft = _draft_resource_to_model(full_draft)
                draft_message_id = (full_draft.get("message") or {}).get("id")
                break
    except Exception:  # noqa: BLE001  # draft lookup is best-effort
        pass

    messages: list[GmailThreadMessage] = []
    for m in thread.get("messages", []) or []:
        msg_id = m.get("id")
        if msg_id and msg_id == draft_message_id:
            continue
        labels = m.get("labelIds") or []
        if "DRAFT" in labels:
            continue
        parsed = _parse_message_resource(m)
        _resolve_inline_images(svc, msg_id or "", parsed)
        messages.append(_thread_message_from_parsed(parsed))

    return GmailThread(
        thread_id=thread.get("id") or input.thread_id,
        messages=messages,
        draft=draft,
    )


def _build_label_lookups(
    svc: Any,
) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """Build label ID→name and name→(bg, text) color maps for tracked labels."""
    label_id_to_name: dict[str, str] = {}
    label_colors: dict[str, tuple[str, str]] = {}
    labels_resp = svc.users().labels().list(userId="me").execute()
    for lbl in labels_resp.get("labels", []):
        name = lbl.get("name")
        if name in _ALL_TRACKED_LABELS:
            label_id_to_name[lbl["id"]] = name
            color = lbl.get("color") or {}
            bg = color.get("backgroundColor", "#f1f3f4")
            text = color.get("textColor", "#444444")
            label_colors[name] = (bg, text)
    return label_id_to_name, label_colors


def _build_draft_thread_map(svc: Any) -> dict[str, str]:
    """Build thread_id → draft_id map from a single drafts.list call."""
    draft_thread_map: dict[str, str] = {}
    try:
        drafts_resp = svc.users().drafts().list(userId="me", maxResults=100).execute()
        for d in drafts_resp.get("drafts", []) or []:
            d_msg = d.get("message") or {}
            tid = d_msg.get("threadId")
            if tid and d.get("id"):
                draft_thread_map[tid] = d["id"]
    except Exception:  # noqa: BLE001  # drafts lookup is best-effort; don't fail curate
        log.debug("drafts.list failed during curate; proceeding without draft info")
    return draft_thread_map


def _thread_has_noise_labels(
    messages: list[dict[str, Any]],
    label_id_to_name: dict[str, str],
) -> bool:
    """Return ``True`` if any message in the thread carries an excluded label."""
    for msg in messages:
        for lid in msg.get("labelIds") or []:
            name = label_id_to_name.get(lid)
            if name and name in _EXCLUDE_LABELS:
                return True
    return False


@service(
    name="gmail_curate_inbox",
    description="Rank recent inbox threads by a deterministic importance score. When an interactive UI is rendered alongside the result, keep your text response brief (a one-line summary) since the user can browse details in the UI. Only elaborate if the user asks.",
    input_model=GmailCurateInboxInput,
    output_model=GmailCurateInboxResult,
)
def gmail_curate_inbox(input: GmailCurateInboxInput) -> GmailCurateInboxResult:
    svc = _get_gmail_client(input.user_id)
    label_id_to_name, label_colors = _build_label_lookups(svc)
    draft_thread_map = _build_draft_thread_map(svc)

    base = (
        "in:inbox -label:MCP-Done"
        " -category:updates -category:promotions -category:social -category:forums"
        " -label:Newsletter -label:Promotion -label:Marketing -label:Notifications"
        ' -label:"Product Updates" -label:"Marketing/Webinar" -label:Webinar'
        ' -label:"Cold Outbound" -label:"NPS Survey" -label:Survey'
    )
    q = f"{base} ({input.query})" if input.query else base
    over_fetch = max(input.limit * 3, 30)
    listing = (
        svc.users().threads().list(userId="me", q=q, maxResults=over_fetch).execute()
    )

    now = datetime.now(UTC)

    thread_ids = [
        stub["id"] for stub in (listing.get("threads", []) or []) if stub.get("id")
    ]
    fetched_threads = (
        _batch_get_threads(
            svc,
            thread_ids,
            metadata_headers=["From", "Subject", "Date"],
        )
        if thread_ids
        else {}
    )

    curated: list[GmailCuratedThread] = []

    for thread_id in thread_ids:
        thread = fetched_threads.get(thread_id)
        if thread is None:
            continue

        messages = thread.get("messages") or []
        if not messages:
            continue

        if _thread_has_noise_labels(messages, label_id_to_name):
            continue

        last_msg = messages[-1]
        headers = _headers_to_dict((last_msg.get("payload") or {}).get("headers"))
        last_message_at = _internal_date_to_dt(last_msg.get("internalDate"))
        all_label_ids: set[str] = set()
        for msg in messages:
            all_label_ids.update(msg.get("labelIds") or [])
        label_ids: list[str] = list(all_label_ids)
        label_names = {
            label_id_to_name[lid] for lid in label_ids if lid in label_id_to_name
        }

        score, reasons, chips = _score_thread(
            label_ids=label_ids,
            label_names=label_names,
            label_colors=label_colors,
            last_message_at=last_message_at,
            now=now,
        )

        tid = thread.get("id") or thread_id
        draft_id = draft_thread_map.get(tid)
        curated.append(
            GmailCuratedThread.model_validate(
                {
                    "thread_id": tid,
                    "subject": headers.get("subject"),
                    "from": headers.get("from"),
                    "snippet": last_msg.get("snippet"),
                    "last_message_at": last_message_at,
                    "importance_score": score,
                    "reasons": reasons,
                    "labels": [c.model_dump() for c in chips],
                    "has_draft": draft_id is not None,
                    "draft_id": draft_id,
                }
            )
        )

    curated.sort(key=lambda t: t.importance_score, reverse=True)
    return GmailCurateInboxResult(threads=curated[: input.limit])


@service(
    name="gmail_mark_thread_read",
    description="Mark a Gmail thread as read by removing the UNREAD label",
    input_model=GmailThreadModifyInput,
    output_model=GmailMarkReadResult,
)
def gmail_mark_thread_read(input: GmailThreadModifyInput) -> GmailMarkReadResult:
    svc = _get_gmail_client(input.user_id)
    svc.users().threads().modify(
        userId="me",
        id=input.thread_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()
    return GmailMarkReadResult(marked_read=True)


@service(
    name="gmail_archive_thread",
    description="Archive a Gmail thread by removing the INBOX label",
    input_model=GmailThreadModifyInput,
    output_model=GmailArchiveResult,
)
def gmail_archive_thread(input: GmailThreadModifyInput) -> GmailArchiveResult:
    svc = _get_gmail_client(input.user_id)
    svc.users().threads().modify(
        userId="me",
        id=input.thread_id,
        body={"removeLabelIds": ["INBOX"]},
    ).execute()
    return GmailArchiveResult(archived=True)


@service(
    name="gmail_mark_thread_done",
    description="Mark a Gmail thread as done by applying the MCP/Done label (hides from curated inbox)",
    input_model=GmailThreadModifyInput,
    output_model=GmailMarkDoneResult,
)
def gmail_mark_thread_done(input: GmailThreadModifyInput) -> GmailMarkDoneResult:
    svc = _get_gmail_client(input.user_id)
    label_id = _get_or_create_mcp_done_label(svc)
    svc.users().threads().modify(
        userId="me",
        id=input.thread_id,
        body={"addLabelIds": [label_id]},
    ).execute()
    return GmailMarkDoneResult(marked_done=True, label_id=label_id)


@service(
    name="gmail_unmark_thread_done",
    description="Remove the MCP/Done label from a thread (undo mark-done)",
    input_model=GmailThreadModifyInput,
    output_model=GmailUnmarkDoneResult,
)
def gmail_unmark_thread_done(input: GmailThreadModifyInput) -> GmailUnmarkDoneResult:
    svc = _get_gmail_client(input.user_id)
    label_id = _find_mcp_done_label(svc)
    if label_id is None:
        return GmailUnmarkDoneResult(unmarked_done=True)
    svc.users().threads().modify(
        userId="me",
        id=input.thread_id,
        body={"removeLabelIds": [label_id]},
    ).execute()
    return GmailUnmarkDoneResult(unmarked_done=True)
