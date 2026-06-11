"""Tests for the Phase 4 headless Gmail services + helpers.

We mock ``_get_gmail_client`` to return a ``MagicMock`` whose chained
``.users().drafts()...`` calls produce canned Gmail API payloads. This
avoids touching ``googleapiclient`` itself (the chained-resource style is
notoriously hard to mock) while still exercising the real service code,
the MIME helpers, and the Pydantic mapping.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db import engine as db_engine
from db.base import Base
from db.models.google_tokens import GoogleToken
from models.gmail import (
    GmailComposeInput,
    GmailCurateInboxInput,
    GmailDiscardDraftInput,
    GmailGetDraftInput,
    GmailGetThreadInput,
    GmailListDraftsInput,
    GmailListInboxInput,
    GmailSendInput,
    GmailUpdateDraftInput,
)
from services.gmail_drafts_svc import (
    GmailReplyInput,
    gmail_compose,
    gmail_discard_draft,
    gmail_get_draft,
    gmail_list_drafts,
    gmail_reply_to_thread,
    gmail_send,
    gmail_update_draft,
)
from services.gmail_messages_svc import (
    GmailThreadModifyInput,
    gmail_archive_thread,
    gmail_curate_inbox,
    gmail_get_thread,
    gmail_list_inbox,
    gmail_mark_thread_read,
)
from services.gmail_svc import (
    GmailNotConnectedError,
    _build_raw_message,
    _get_gmail_client,
    _parse_message_resource,
)
from tests.test_template import TestTemplate

# ---------------------------------------------------------------------------
# DB fixture (same pattern as tests/test_google_oauth.py)
# ---------------------------------------------------------------------------


@contextmanager
def _patch_db():
    orig_engine = db_engine._engine
    orig_session = db_engine._SessionLocal
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    session_factory = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    db_engine._engine = eng
    db_engine._SessionLocal = session_factory
    try:
        yield session_factory
    finally:
        db_engine._engine = orig_engine
        db_engine._SessionLocal = orig_session


def _seed_token(factory, user_id: str = "alice") -> None:
    s = factory()
    s.add(
        GoogleToken(
            user_id=user_id,
            email=f"{user_id}@example.com",
            refresh_token_enc=b"RT",
            key_id="plaintext",
            scopes=["openid", "email"],
        )
    )
    s.commit()
    s.close()


# ---------------------------------------------------------------------------
# Helpers for building fake Gmail API payloads
# ---------------------------------------------------------------------------


def _b64url(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii")


def _headers(d: dict[str, str]) -> list[dict[str, str]]:
    return [{"name": k, "value": v} for k, v in d.items()]


def _plain_message(
    *,
    message_id: str = "m-1",
    thread_id: str = "t-1",
    headers: dict[str, str] | None = None,
    body: str = "hello world",
    snippet: str = "hello world",
    internal_date_ms: int | None = None,
    label_ids: list[str] | None = None,
) -> dict:
    return {
        "id": message_id,
        "threadId": thread_id,
        "snippet": snippet,
        "internalDate": str(internal_date_ms) if internal_date_ms else "1700000000000",
        "labelIds": label_ids or [],
        "payload": {
            "mimeType": "text/plain",
            "headers": _headers(
                headers or {"From": "a@x", "To": "b@y", "Subject": "hi"}
            ),
            "body": {"data": _b64url(body), "size": len(body)},
        },
    }


def _draft_resource(
    *,
    draft_id: str = "d-1",
    to: str = "b@y",
    subject: str = "hi",
    body: str = "hello world",
    thread_id: str = "t-1",
) -> dict:
    return {
        "id": draft_id,
        "message": _plain_message(
            message_id=f"m-{draft_id}",
            thread_id=thread_id,
            headers={"To": to, "Subject": subject},
            body=body,
            snippet=body[:50],
        ),
    }


def _make_mock_service() -> MagicMock:
    """A MagicMock that supports the chained ``.users().drafts().get().execute()`` style."""
    mock = MagicMock()
    mock.users().labels().list().execute.return_value = {"labels": []}
    mock.users().drafts().list().execute.return_value = {"drafts": []}
    return mock


# ---------------------------------------------------------------------------
# MIME helper tests (no Gmail mock needed)
# ---------------------------------------------------------------------------


class TestBuildRawMessage(TestTemplate):
    def test_round_trip(self):
        raw = _build_raw_message(
            to="b@example.com",
            subject="Subject!",
            body="Body text\nwith newline",
            cc="cc@example.com",
        )
        decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
        msg = message_from_bytes(decoded)
        assert msg["To"] == "b@example.com"
        assert msg["Cc"] == "cc@example.com"
        assert msg["Subject"] == "Subject!"
        # get_payload(decode=True) returns the decoded body bytes for a
        # non-multipart message; cast for the type-checker.
        raw_payload = msg.get_payload(decode=True)
        assert isinstance(raw_payload, bytes)
        payload = raw_payload.decode("utf-8")
        assert "Body text" in payload
        assert "with newline" in payload


class TestParseMessageResource(TestTemplate):
    def test_plaintext_only(self):
        msg = _plain_message(body="plain body", headers={"From": "a@x", "Subject": "s"})
        parsed = _parse_message_resource(msg)
        assert parsed["body_text"] == "plain body"
        assert parsed["body_html"] is None
        assert parsed["from"] == "a@x"
        assert parsed["subject"] == "s"
        assert parsed["attachments"] == []
        assert isinstance(parsed["date"], datetime)

    def test_html_only(self):
        msg = {
            "id": "m",
            "threadId": "t",
            "snippet": "snip",
            "internalDate": "1700000000000",
            "payload": {
                "mimeType": "text/html",
                "headers": _headers({"Subject": "html"}),
                "body": {"data": _b64url("<p>hi</p>"), "size": 9},
            },
        }
        parsed = _parse_message_resource(msg)
        assert parsed["body_text"] is None
        assert parsed["body_html"] == "<p>hi</p>"

    def test_multipart_with_attachment(self):
        msg = {
            "id": "m",
            "threadId": "t",
            "snippet": "snip",
            "internalDate": "1700000000000",
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": _headers({"From": "a@x"}),
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": _b64url("text part"), "size": 9},
                    },
                    {
                        "mimeType": "text/html",
                        "body": {"data": _b64url("<p>html part</p>"), "size": 16},
                    },
                    {
                        "mimeType": "application/pdf",
                        "filename": "report.pdf",
                        "body": {"attachmentId": "att-1", "size": 1024},
                    },
                ],
            },
        }
        parsed = _parse_message_resource(msg)
        assert parsed["body_text"] == "text part"
        assert parsed["body_html"] == "<p>html part</p>"
        assert len(parsed["attachments"]) == 1
        att = parsed["attachments"][0]
        assert att["filename"] == "report.pdf"
        assert att["mime_type"] == "application/pdf"
        assert att["size"] == 1024
        assert att["attachment_id"] == "att-1"


# ---------------------------------------------------------------------------
# Service tests (mock _get_gmail_client)
# ---------------------------------------------------------------------------


def _patch_client(mock_svc: MagicMock):
    # Patch every import site so each service module picks it up.
    return [
        patch("services.gmail_svc._get_gmail_client", return_value=mock_svc),
        patch("services.gmail_drafts_svc._get_gmail_client", return_value=mock_svc),
        patch("services.gmail_messages_svc._get_gmail_client", return_value=mock_svc),
    ]


def _apply(patches):
    return [p.start() for p in patches]


def _stop(patches):
    for p in patches:
        p.stop()


class TestGmailListDrafts(TestTemplate):
    def test_happy_path(self):
        draft_payloads = [
            {
                "id": "d-1",
                "message": {
                    "id": "m-d-1",
                    "snippet": "Hi Alice",
                    "internalDate": "1700000000000",
                    "payload": {
                        "headers": _headers(
                            {"To": "alice@example.com", "Subject": "Hello"}
                        ),
                    },
                },
            },
            {
                "id": "d-2",
                "message": {
                    "id": "m-d-2",
                    "snippet": "Hi Bob",
                    "internalDate": "1700000001000",
                    "payload": {
                        "headers": _headers(
                            {"To": "bob@example.com", "Subject": "Hey"}
                        ),
                    },
                },
            },
        ]

        class FakeBatch:
            def __init__(self):
                self._queue: list[tuple] = []

            def add(self, req, callback):
                self._queue.append((req, callback))

            def execute(self):
                for i, (_req, cb) in enumerate(self._queue):
                    cb(str(i), draft_payloads[i], None)

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().list().execute.return_value = {
                "drafts": [{"id": "d-1"}, {"id": "d-2"}],
            }
            mock.new_batch_http_request.return_value = FakeBatch()

            patches = _patch_client(mock)
            _apply(patches)
            try:
                result = gmail_list_drafts(
                    GmailListDraftsInput(user_id="alice", limit=10)
                )
            finally:
                _stop(patches)

        assert len(result.drafts) == 2
        assert result.drafts[0].draft_id == "d-1"
        assert result.drafts[0].to == "alice@example.com"
        assert result.drafts[0].subject == "Hello"


class TestGmailGetDraft(TestTemplate):
    def test_happy_path(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().get().execute.return_value = _draft_resource(
                draft_id="d-1",
                to="alice@example.com",
                subject="Hello",
                body="The body",
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                draft = gmail_get_draft(
                    GmailGetDraftInput(user_id="alice", draft_id="d-1")
                )
            finally:
                _stop(patches)

        assert draft.draft_id == "d-1"
        assert draft.to == "alice@example.com"
        assert draft.subject == "Hello"
        assert draft.body == "The body"


class TestGmailUpdateDraft(TestTemplate):
    def test_only_body_patched_preserves_to_and_subject(self):
        original = _draft_resource(
            draft_id="d-1",
            to="alice@example.com",
            subject="Original Subject",
            body="Original body",
        )
        updated_resource = _draft_resource(
            draft_id="d-1",
            to="alice@example.com",
            subject="Original Subject",
            body="New body",
        )

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().get().execute.return_value = original
            mock.users().drafts().update().execute.return_value = updated_resource

            patches = _patch_client(mock)
            _apply(patches)
            try:
                draft = gmail_update_draft(
                    GmailUpdateDraftInput(
                        user_id="alice", draft_id="d-1", body="New body"
                    )
                )
            finally:
                _stop(patches)

        # Verify the update call carried over the preserved fields by decoding
        # the raw MIME passed to drafts().update(body=...).
        update_calls = [
            c for c in mock.users().drafts().update.call_args_list if c.kwargs
        ]
        assert update_calls, "drafts().update() was not called with kwargs"
        last = update_calls[-1]
        raw_b64 = last.kwargs["body"]["message"]["raw"]
        mime = message_from_bytes(base64.urlsafe_b64decode(raw_b64.encode("ascii")))
        assert mime["To"] == "alice@example.com"
        assert mime["Subject"] == "Original Subject"
        decoded_payload = mime.get_payload(decode=True)
        assert isinstance(decoded_payload, bytes)
        assert "New body" in decoded_payload.decode("utf-8")

        assert draft.draft_id == "d-1"
        assert draft.to == "alice@example.com"
        assert draft.subject == "Original Subject"
        assert draft.body == "New body"


class TestGmailCompose(TestTemplate):
    def test_returns_populated_draft(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().create().execute.return_value = _draft_resource(
                draft_id="d-new",
                to="alice@example.com",
                subject="Subj",
                body="Body!",
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                draft = gmail_compose(
                    GmailComposeInput(
                        user_id="alice",
                        to="alice@example.com",
                        subject="Subj",
                        body="Body!",
                    )
                )
            finally:
                _stop(patches)

        assert draft.draft_id == "d-new"
        assert draft.to == "alice@example.com"
        assert draft.subject == "Subj"
        assert draft.body == "Body!"


class TestGmailSend(TestTemplate):
    def test_happy_path(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().send().execute.return_value = {
                "id": "msg-123",
                "threadId": "thr-7",
                "labelIds": ["SENT"],
            }
            patches = _patch_client(mock)
            _apply(patches)
            try:
                before = datetime.now(UTC)
                result = gmail_send(GmailSendInput(user_id="alice", draft_id="d-1"))
                after = datetime.now(UTC)
            finally:
                _stop(patches)

        assert result.message_id == "msg-123"
        assert result.thread_id == "thr-7"
        assert before <= result.sent_at <= after


class TestGmailDiscardDraft(TestTemplate):
    def test_happy_path(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().delete().execute.return_value = {}
            patches = _patch_client(mock)
            _apply(patches)
            try:
                result = gmail_discard_draft(
                    GmailDiscardDraftInput(user_id="alice", draft_id="d-1")
                )
            finally:
                _stop(patches)

        assert result.discarded is True


# ---------------------------------------------------------------------------
# Inbox / threads / curate
# ---------------------------------------------------------------------------


class TestGmailListInbox(TestTemplate):
    def test_happy_path_uses_from_alias(self):
        msg_payloads = {
            "m-1": {
                "id": "m-1",
                "threadId": "t-1",
                "snippet": "snip 1",
                "internalDate": "1700000000000",
                "payload": {
                    "headers": _headers(
                        {
                            "From": "sender1@example.com",
                            "Subject": "S1",
                            "Date": "Wed, 15 Nov 2023 00:00:00 +0000",
                        }
                    )
                },
            },
            "m-2": {
                "id": "m-2",
                "threadId": "t-2",
                "snippet": "snip 2",
                "internalDate": "1700000001000",
                "payload": {
                    "headers": _headers(
                        {"From": "sender2@example.com", "Subject": "S2"}
                    )
                },
            },
        }

        def fake_batch_get_messages(svc, ids, **kwargs):
            return {mid: msg_payloads[mid] for mid in ids if mid in msg_payloads}

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().messages().list().execute.return_value = {
                "messages": [{"id": "m-1"}, {"id": "m-2"}],
            }
            patches = _patch_client(mock)
            _apply(patches)
            with patch(
                "services.gmail_messages_svc._batch_get_messages",
                side_effect=fake_batch_get_messages,
            ):
                try:
                    result = gmail_list_inbox(
                        GmailListInboxInput(user_id="alice", limit=5)
                    )
                finally:
                    _stop(patches)

        assert len(result.messages) == 2
        assert result.messages[0].from_ == "sender1@example.com"
        assert result.messages[0].subject == "S1"
        dumped = result.messages[0].model_dump(by_alias=True)
        assert dumped["from"] == "sender1@example.com"


class TestGmailGetThread(TestTemplate):
    def test_thread_with_two_messages_and_attachment(self):
        # Build a thread payload directly
        thread_payload = {
            "id": "t-9",
            "messages": [
                _plain_message(
                    message_id="m-a",
                    thread_id="t-9",
                    headers={
                        "From": "a@x.com",
                        "To": "b@y.com",
                        "Subject": "Re: stuff",
                    },
                    body="first",
                ),
                {
                    "id": "m-b",
                    "threadId": "t-9",
                    "snippet": "second",
                    "internalDate": "1700000005000",
                    "payload": {
                        "mimeType": "multipart/mixed",
                        "headers": _headers(
                            {
                                "From": "b@y.com",
                                "To": "a@x.com",
                                "Subject": "Re: stuff",
                            }
                        ),
                        "parts": [
                            {
                                "mimeType": "text/plain",
                                "body": {"data": _b64url("reply body"), "size": 10},
                            },
                            {
                                "mimeType": "application/pdf",
                                "filename": "doc.pdf",
                                "body": {"attachmentId": "att-9", "size": 42},
                            },
                        ],
                    },
                },
            ],
        }

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().threads().get().execute.return_value = thread_payload
            patches = _patch_client(mock)
            _apply(patches)
            try:
                thread = gmail_get_thread(
                    GmailGetThreadInput(user_id="alice", thread_id="t-9")
                )
            finally:
                _stop(patches)

        assert thread.thread_id == "t-9"
        assert len(thread.messages) == 2
        assert thread.messages[0].body_text == "first"
        assert thread.messages[0].from_ == "a@x.com"
        assert thread.messages[1].body_text == "reply body"
        assert len(thread.messages[1].attachments) == 1
        assert thread.messages[1].attachments[0].filename == "doc.pdf"
        assert thread.messages[1].attachments[0].attachment_id == "att-9"


class TestGmailCurateInbox(TestTemplate):
    def test_ranks_by_score_deterministically(self):
        now = datetime.now(UTC)
        thread_a = {
            "id": "tA",
            "messages": [
                {
                    "id": "mA",
                    "labelIds": ["IMPORTANT", "UNREAD"],
                    "snippet": "vip!",
                    "internalDate": str(
                        int((now - timedelta(hours=1)).timestamp() * 1000)
                    ),
                    "payload": {
                        "headers": _headers(
                            {"From": "ceo@example.com", "Subject": "VIP"}
                        )
                    },
                },
            ],
        }
        thread_b = {
            "id": "tB",
            "messages": [
                {
                    "id": "mB",
                    "labelIds": ["UNREAD"],
                    "snippet": "meh",
                    "internalDate": str(
                        int((now - timedelta(days=3)).timestamp() * 1000)
                    ),
                    "payload": {
                        "headers": _headers(
                            {"From": "friend@example.com", "Subject": "hi"}
                        )
                    },
                },
            ],
        }
        thread_c = {
            "id": "tC",
            "messages": [
                {
                    "id": "mC",
                    "labelIds": [],
                    "snippet": "ancient",
                    "internalDate": str(
                        int((now - timedelta(days=14)).timestamp() * 1000)
                    ),
                    "payload": {
                        "headers": _headers(
                            {"From": "old@example.com", "Subject": "old"}
                        )
                    },
                },
            ],
        }
        thread_map = {"tA": thread_a, "tB": thread_b, "tC": thread_c}

        def fake_batch_get_threads(svc, ids, **kwargs):
            return {tid: thread_map[tid] for tid in ids if tid in thread_map}

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().threads().list().execute.return_value = {
                "threads": [{"id": "tA"}, {"id": "tB"}, {"id": "tC"}],
            }
            patches = _patch_client(mock)
            _apply(patches)
            with patch(
                "services.gmail_messages_svc._batch_get_threads",
                side_effect=fake_batch_get_threads,
            ):
                try:
                    result = gmail_curate_inbox(
                        GmailCurateInboxInput(user_id="alice", limit=10)
                    )
                finally:
                    _stop(patches)

        ids = [t.thread_id for t in result.threads]
        assert ids == ["tA", "tB", "tC"]
        assert result.threads[0].importance_score > result.threads[1].importance_score
        assert result.threads[1].importance_score > result.threads[2].importance_score
        dumped = result.threads[0].model_dump(by_alias=True)
        assert dumped["from"] == "ceo@example.com"

    def test_skips_thread_when_batch_fetch_omits_it(self):
        """Threads missing from the batch result (e.g. deleted) are silently skipped."""
        good_thread = {
            "id": "good",
            "messages": [
                {
                    "id": "mG",
                    "labelIds": ["IMPORTANT"],
                    "snippet": "ok",
                    "internalDate": "1700000000000",
                    "payload": {"headers": _headers({"Subject": "ok"})},
                },
            ],
        }

        def fake_batch_get_threads(svc, ids, **kwargs):
            return {"good": good_thread}

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().threads().list().execute.return_value = {
                "threads": [{"id": "good"}, {"id": "bad"}],
            }
            patches = _patch_client(mock)
            _apply(patches)
            with patch(
                "services.gmail_messages_svc._batch_get_threads",
                side_effect=fake_batch_get_threads,
            ):
                try:
                    result = gmail_curate_inbox(
                        GmailCurateInboxInput(user_id="alice", limit=10)
                    )
                finally:
                    _stop(patches)

        assert [t.thread_id for t in result.threads] == ["good"]


class TestGmailNotConnected(TestTemplate):
    def test_raises_when_no_row(self):
        with _patch_db(), pytest.raises(GmailNotConnectedError):
            _get_gmail_client("nonexistent-user")


class TestDraftRoundTrip(TestTemplate):
    def test_compose_get_update_send_records_call_order(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()

            mock.users().drafts().create().execute.return_value = _draft_resource(
                draft_id="d-new", to="alice@example.com", subject="S", body="B"
            )
            mock.users().drafts().get().execute.return_value = _draft_resource(
                draft_id="d-new", to="alice@example.com", subject="S", body="B"
            )
            mock.users().drafts().update().execute.return_value = _draft_resource(
                draft_id="d-new",
                to="alice@example.com",
                subject="S",
                body="B-updated",
            )
            mock.users().drafts().send().execute.return_value = {
                "id": "msg-final",
                "threadId": "t-1",
            }

            patches = _patch_client(mock)
            _apply(patches)
            try:
                composed = gmail_compose(
                    GmailComposeInput(
                        user_id="alice",
                        to="alice@example.com",
                        subject="S",
                        body="B",
                    )
                )
                fetched = gmail_get_draft(
                    GmailGetDraftInput(user_id="alice", draft_id=composed.draft_id)
                )
                updated = gmail_update_draft(
                    GmailUpdateDraftInput(
                        user_id="alice",
                        draft_id=composed.draft_id,
                        body="B-updated",
                    )
                )
                sent = gmail_send(
                    GmailSendInput(user_id="alice", draft_id=composed.draft_id)
                )
            finally:
                _stop(patches)

        assert composed.draft_id == "d-new"
        assert fetched.draft_id == "d-new"
        assert updated.body == "B-updated"
        assert sent.message_id == "msg-final"


# ---------------------------------------------------------------------------
# Mark-read / Archive / Reply (Phase 6 additions)
# ---------------------------------------------------------------------------


class TestGmailMarkThreadRead(TestTemplate):
    def test_calls_threads_modify_with_remove_unread_label(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().threads().modify().execute.return_value = {"id": "t-1"}
            patches = _patch_client(mock)
            _apply(patches)
            try:
                result = gmail_mark_thread_read(
                    GmailThreadModifyInput(user_id="alice", thread_id="t-1")
                )
            finally:
                _stop(patches)

        assert result.marked_read is True
        modify_calls = [
            c for c in mock.users().threads().modify.call_args_list if c.kwargs
        ]
        assert modify_calls, "threads().modify() was not called with kwargs"
        last = modify_calls[-1]
        assert last.kwargs["id"] == "t-1"
        assert last.kwargs["body"] == {"removeLabelIds": ["UNREAD"]}


class TestGmailArchiveThread(TestTemplate):
    def test_calls_threads_modify_with_remove_inbox_label(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().threads().modify().execute.return_value = {"id": "t-2"}
            patches = _patch_client(mock)
            _apply(patches)
            try:
                result = gmail_archive_thread(
                    GmailThreadModifyInput(user_id="alice", thread_id="t-2")
                )
            finally:
                _stop(patches)

        assert result.archived is True
        modify_calls = [
            c for c in mock.users().threads().modify.call_args_list if c.kwargs
        ]
        assert modify_calls
        last = modify_calls[-1]
        assert last.kwargs["id"] == "t-2"
        assert last.kwargs["body"] == {"removeLabelIds": ["INBOX"]}


class TestGmailReplyToThread(TestTemplate):
    def _patch_reply(self, *, last_msg_headers: dict[str, str], created_draft: dict):
        mock = _make_mock_service()
        thread_payload = {
            "id": "t-rep",
            "messages": [
                {
                    "id": "m-last",
                    "internalDate": "1700000000000",
                    "payload": {"headers": _headers(last_msg_headers)},
                }
            ],
        }
        mock.users().threads().get().execute.return_value = thread_payload
        mock.users().drafts().create().execute.return_value = created_draft
        return mock

    def test_derives_to_and_subject_from_last_message(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = self._patch_reply(
                last_msg_headers={
                    "From": "sender@example.com",
                    "Subject": "Original Subject",
                },
                created_draft=_draft_resource(
                    draft_id="d-rep",
                    to="sender@example.com",
                    subject="Re: Original Subject",
                    body="",
                    thread_id="t-rep",
                ),
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                draft = gmail_reply_to_thread(
                    GmailReplyInput(user_id="alice", thread_id="t-rep")
                )
            finally:
                _stop(patches)

        assert draft.draft_id == "d-rep"
        # Verify the MIME built carried derived To/Subject + the threadId was
        # threaded onto drafts().create.
        create_calls = [
            c for c in mock.users().drafts().create.call_args_list if c.kwargs
        ]
        assert create_calls
        body = create_calls[-1].kwargs["body"]
        assert body["message"]["threadId"] == "t-rep"
        raw_b64 = body["message"]["raw"]
        mime = message_from_bytes(base64.urlsafe_b64decode(raw_b64.encode("ascii")))
        assert mime["To"] == "sender@example.com"
        assert mime["Subject"] == "Re: Original Subject"

    def test_does_not_double_prefix_re_when_already_present(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = self._patch_reply(
                last_msg_headers={
                    "From": "sender@example.com",
                    "Subject": "Re: already a reply",
                },
                created_draft=_draft_resource(
                    draft_id="d-rep2",
                    to="sender@example.com",
                    subject="Re: already a reply",
                    body="",
                ),
            )
            patches = _patch_client(mock)
            _apply(patches)
            try:
                gmail_reply_to_thread(
                    GmailReplyInput(user_id="alice", thread_id="t-rep")
                )
            finally:
                _stop(patches)

        create_calls = [
            c for c in mock.users().drafts().create.call_args_list if c.kwargs
        ]
        raw_b64 = create_calls[-1].kwargs["body"]["message"]["raw"]
        mime = message_from_bytes(base64.urlsafe_b64decode(raw_b64.encode("ascii")))
        assert mime["Subject"] == "Re: already a reply"

    def test_raises_when_thread_has_no_messages(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().threads().get().execute.return_value = {
                "id": "t-empty",
                "messages": [],
            }
            patches = _patch_client(mock)
            _apply(patches)
            try:
                with pytest.raises(ValueError, match="no messages"):
                    gmail_reply_to_thread(
                        GmailReplyInput(user_id="alice", thread_id="t-empty")
                    )
            finally:
                _stop(patches)
