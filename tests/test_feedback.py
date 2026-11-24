"""Tests for the agent ``feedback`` service, its sinks, and the CLI command."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import get_args
from unittest.mock import patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

from common import global_config
from db import engine as db_engine
from db.base import Base
from db.models.feedback import AgentFeedback
from models.feedback import FeedbackCategory, FeedbackInput
from services import feedback_svc
from services.feedback_svc import (
    FeedbackRecord,
    SlackSinkError,
    SlackWebhookSink,
    _slack_escape,
    feedback,
    register_feedback_sink,
)
from src.cli.app import _register_builtin_commands, _register_user_commands, app
from src.cli.commands.feedback import _CATEGORIES
from tests.test_template import TestTemplate

runner = CliRunner()
_register_builtin_commands()
_register_user_commands()


@contextmanager
def _patch_db():
    orig_engine, orig_session = db_engine._engine, db_engine._SessionLocal
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    db_engine._engine, db_engine._SessionLocal = eng, factory
    try:
        yield factory
    finally:
        db_engine._engine, db_engine._SessionLocal = orig_engine, orig_session


@contextmanager
def _isolated_sinks():
    # Also blank the Linear secrets so a developer's real .env never makes a
    # test call Linear; tests that want the sink patch them back in.
    saved = list(feedback_svc._sinks)
    feedback_svc._sinks.clear()
    try:
        with (
            patch.object(global_config, "LINEAR_API_KEY", None),
            patch.object(global_config, "LINEAR_TEAM_ID", None),
        ):
            yield
    finally:
        feedback_svc._sinks[:] = saved


class _RecordingSink:
    def __init__(self, name: str = "recorder", fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.records: list[FeedbackRecord] = []

    def send(self, record: FeedbackRecord) -> None:
        if self.fail:
            raise RuntimeError("sink down")
        self.records.append(record)


class TestFeedbackService(TestTemplate):
    def test_stores_row_with_no_sinks(self):
        with _patch_db() as factory, _isolated_sinks():
            with patch.object(global_config, "FEEDBACK_SLACK_WEBHOOK_URL", None):
                result = feedback(
                    FeedbackInput(
                        user_id="u1",
                        message="stuck",
                        category="bug",
                        tool_name="gmail_send",
                    )
                )
            assert result.forwarded_to == []
            with factory() as s:
                row = s.get(AgentFeedback, result.id)
            assert row is not None
            assert (row.user_id, row.category, row.tool_name) == (
                "u1",
                "bug",
                "gmail_send",
            )

    def test_failing_sink_does_not_fail_call(self):
        good, bad = _RecordingSink("good"), _RecordingSink("bad", fail=True)
        with _patch_db(), _isolated_sinks():
            register_feedback_sink(bad)
            register_feedback_sink(good)
            with patch.object(global_config, "FEEDBACK_SLACK_WEBHOOK_URL", None):
                result = feedback(FeedbackInput(user_id="u1", message="hi"))
        assert result.forwarded_to == ["good"]
        assert good.records[0].message == "hi"

    def test_register_replaces_same_name(self):
        first, second = _RecordingSink("x"), _RecordingSink("x")
        with _isolated_sinks():
            register_feedback_sink(first)
            register_feedback_sink(second)
            assert feedback_svc._sinks == [second]

    def test_slack_sink_enabled_by_env_url(self):
        posted: list[dict] = []

        def fake_post(url, json, **_kwargs):  # noqa: A002 - mirrors httpx.post
            posted.append({"url": url, "json": json})
            return httpx.Response(200, request=httpx.Request("POST", url))

        with (
            _patch_db(),
            _isolated_sinks(),
            patch.object(
                global_config,
                "FEEDBACK_SLACK_WEBHOOK_URL",
                "https://hooks.slack.test/x",
            ),
            patch("services.feedback_svc.httpx.post", fake_post),
        ):
            result = feedback(FeedbackInput(user_id="u1", message="<!channel> help"))
        assert result.forwarded_to == ["slack"]
        assert posted[0]["url"] == "https://hooks.slack.test/x"
        text = posted[0]["json"]["text"]
        assert "<!channel>" not in text
        assert "&lt;!channel&gt;" in text

    def test_slack_sink_raises_on_http_error(self):
        def fake_post(url, json, **_kwargs):  # noqa: A002
            return httpx.Response(500, request=httpx.Request("POST", url))

        record = FeedbackRecord(
            id="1",
            user_id="",
            category="other",
            message="m",
            tool_name=None,
            context=None,
            created_at=None,  # ty: ignore[invalid-argument-type]
        )
        with patch("services.feedback_svc.httpx.post", fake_post):
            try:
                SlackWebhookSink("https://hooks.slack.test/x").send(record)
            except SlackSinkError as exc:
                assert "hooks.slack.test" not in str(exc)
                assert exc.__suppress_context__
                return
        raise AssertionError("expected SlackSinkError")

    def test_slack_escape(self):
        assert _slack_escape("a & <b|c> <!here>") == "a &amp; &lt;b|c&gt; &lt;!here&gt;"


class TestFeedbackRegistration(TestTemplate):
    def test_registered_as_mutating_tool(self):
        from mcp_server.server import llm_tool_surface  # noqa: PLC0415

        entry = next(e for e in llm_tool_surface() if e.name == "feedback")
        assert entry.mutating is True

    def test_cli_categories_match_model(self):
        assert set(_CATEGORIES) == set(get_args(FeedbackCategory))


class TestFeedbackCli(TestTemplate):
    def test_message_argument(self):
        args = ["--format", "json", "feedback", "docs unclear", "-c", "confusing_docs"]
        with (
            _patch_db(),
            _isolated_sinks(),
            patch.object(global_config, "FEEDBACK_SLACK_WEBHOOK_URL", None),
        ):
            res = runner.invoke(app, args)
        assert res.exit_code == 0, res.output
        assert json.loads(res.stdout)["forwarded_to"] == []

    def test_stdin(self):
        with _patch_db() as factory, _isolated_sinks():
            with patch.object(global_config, "FEEDBACK_SLACK_WEBHOOK_URL", None):
                res = runner.invoke(app, ["feedback", "--stdin"], input="from a pipe\n")
            assert res.exit_code == 0, res.output
            with factory() as s:
                assert s.query(AgentFeedback).one().message.strip() == "from a pipe"

    def test_dry_run_writes_nothing(self):
        with _patch_db() as factory:
            res = runner.invoke(app, ["--dry-run", "feedback", "x"])
            assert res.exit_code == 0
            with factory() as s:
                assert s.query(AgentFeedback).count() == 0

    def test_missing_message_fails_fast(self):
        res = runner.invoke(app, ["feedback"], input="")
        assert res.exit_code == 1

    def test_bad_category_fails(self):
        res = runner.invoke(app, ["feedback", "x", "-c", "nope"])
        assert res.exit_code == 1
