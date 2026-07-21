"""Gating tests for the e2e-only fake Gmail backend.

The fake (``services/_gmail_fake_backend.py``) lets the MCP-App e2e harness render
the Gmail apps offline. These tests pin the two invariants that keep it safe:
it is off unless explicitly enabled, and it hard-refuses in production - plus a
happy-path check that, when enabled, ``gmail_get_thread`` returns a real parsed
``GmailThread`` built from the fixtures (no linked account / OAuth / network).
"""

from __future__ import annotations

import pytest

from common import global_config
from models.gmail import GmailGetThreadInput
from services import gmail_svc
from services.gmail_messages_svc import gmail_get_thread
from tests.test_template import TestTemplate


class TestGmailFakeBackend(TestTemplate):
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("GMAIL_FAKE_BACKEND", raising=False)
        assert gmail_svc._maybe_fake_gmail_client() is None

    def test_flag_other_than_1_stays_disabled(self, monkeypatch):
        monkeypatch.setenv("GMAIL_FAKE_BACKEND", "true")
        assert gmail_svc._maybe_fake_gmail_client() is None

    def test_enabled_serves_fixture_thread(self, monkeypatch):
        monkeypatch.setenv("GMAIL_FAKE_BACKEND", "1")
        monkeypatch.setattr(global_config, "DEV_ENV", "dev", raising=False)
        thread = gmail_get_thread(GmailGetThreadInput(thread_id="t-1001"))
        assert thread.thread_id == "t-1001"
        assert len(thread.messages) == 1
        msg = thread.messages[0]
        assert msg.subject == "Series A term sheet - final redlines"
        assert msg.from_ is not None and "Dana Whitfield" in msg.from_
        # Real service parsing ran end-to-end: the html body + attachment metadata
        # came through the raw-Gmail-API fixture, not a hand-built GmailThread.
        assert msg.body_html is not None and "liquidation preference" in msg.body_html
        assert [a.filename for a in msg.attachments] == ["termsheet-v7.pdf"]

    def test_refused_in_prod(self, monkeypatch):
        monkeypatch.setenv("GMAIL_FAKE_BACKEND", "1")
        monkeypatch.setattr(global_config, "DEV_ENV", "prod", raising=False)
        with pytest.raises(RuntimeError, match="production"):
            gmail_svc._maybe_fake_gmail_client()
