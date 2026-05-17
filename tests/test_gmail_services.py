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
    GmailGetDraftInput,
    GmailGetThreadInput,
    GmailListDraftsInput,
    GmailListInboxInput,
    GmailSendInput,
    GmailUpdateDraftInput,
)
from services.gmail_drafts_svc import (
    gmail_compose,
    gmail_get_draft,
    gmail_list_drafts,
    gmail_send,
    gmail_update_draft,
)
from services.gmail_messages_svc import (
    gmail_curate_inbox,
    gmail_get_thread,
    gmail_list_inbox,
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
    return MagicMock()


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
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().drafts().list().execute.return_value = {
                "drafts": [{"id": "d-1"}, {"id": "d-2"}],
            }
            mock.users().drafts().get().execute.side_effect = [
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


# ---------------------------------------------------------------------------
# Inbox / threads / curate
# ---------------------------------------------------------------------------


class TestGmailListInbox(TestTemplate):
    def test_happy_path_uses_from_alias(self):
        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().messages().list().execute.return_value = {
                "messages": [{"id": "m-1"}, {"id": "m-2"}],
            }
            mock.users().messages().get().execute.side_effect = [
                {
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
                {
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
            ]
            patches = _patch_client(mock)
            _apply(patches)
            try:
                result = gmail_list_inbox(GmailListInboxInput(user_id="alice", limit=5))
            finally:
                _stop(patches)

        assert len(result.messages) == 2
        # The alias 'from' is exposed as field from_
        assert result.messages[0].from_ == "sender1@example.com"
        assert result.messages[0].subject == "S1"
        # Serializing with by_alias=True must emit 'from'
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
        # Thread A: IMPORTANT + UNREAD + recent (1h)
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
        # Thread B: UNREAD only, 3 days old
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
        # Thread C: no special labels, 2 weeks old (no recency boost)
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

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().threads().list().execute.return_value = {
                "threads": [{"id": "tA"}, {"id": "tB"}, {"id": "tC"}],
            }
            mock.users().threads().get().execute.side_effect = [
                thread_a,
                thread_b,
                thread_c,
            ]
            patches = _patch_client(mock)
            _apply(patches)
            try:
                result = gmail_curate_inbox(
                    GmailCurateInboxInput(user_id="alice", limit=10)
                )
            finally:
                _stop(patches)

        ids = [t.thread_id for t in result.threads]
        assert ids == ["tA", "tB", "tC"]
        # A has IMPORTANT + UNREAD + strong recency
        assert result.threads[0].importance_score > result.threads[1].importance_score
        assert result.threads[1].importance_score > result.threads[2].importance_score
        # alias roundtrip
        dumped = result.threads[0].model_dump(by_alias=True)
        assert dumped["from"] == "ceo@example.com"

    def test_skips_thread_when_metadata_fetch_raises_http_error(self):
        from googleapiclient.errors import HttpError

        with _patch_db() as factory:
            _seed_token(factory)
            mock = _make_mock_service()
            mock.users().threads().list().execute.return_value = {
                "threads": [{"id": "good"}, {"id": "bad"}],
            }

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
            fake_resp = MagicMock()
            fake_resp.status = 404
            fake_resp.reason = "Not Found"
            err = HttpError(fake_resp, b"not found")

            mock.users().threads().get().execute.side_effect = [good_thread, err]
            patches = _patch_client(mock)
            _apply(patches)
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
