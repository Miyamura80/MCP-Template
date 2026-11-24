"""Tests for the agent ``feedback`` service, the Slack sink, and the CLI command.

Sink secrets are blanked for the whole run by ``tests/conftest.py``; the
``feedback_db``, ``isolated_sinks`` and ``sink_http`` fixtures live there too.
"""

from __future__ import annotations

import json
from typing import get_args
from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from common import global_config
from db.models.feedback import AgentFeedback
from models.feedback import FeedbackCategory, FeedbackInput
from services import feedback_svc
from services.feedback_sinks import FeedbackRecord, SinkError, SlackWebhookSink
from services.feedback_sinks.slack import slack_escape
from services.feedback_svc import feedback, register_feedback_sink
from src.cli.app import _register_builtin_commands, _register_user_commands, app
from src.cli.commands.feedback import _CATEGORIES
from tests.test_template import TestTemplate

runner = CliRunner()
_register_builtin_commands()
_register_user_commands()

_SLACK_URL = "https://hooks.slack.test/x"


class _RecordingSink:
    def __init__(self, name: str = "recorder", fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.records: list[FeedbackRecord] = []

    def send(self, record: FeedbackRecord) -> None:
        if self.fail:
            raise RuntimeError("sink down")
        self.records.append(record)


def _record() -> FeedbackRecord:
    return FeedbackRecord(
        id="1",
        user_id="",
        category="other",
        message="m",
        tool_name=None,
        context=None,
        created_at=None,  # ty: ignore[invalid-argument-type]
    )


class TestFeedbackService(TestTemplate):
    @pytest.mark.usefixtures("isolated_sinks")
    def test_stores_row_with_no_sinks(self, feedback_db):
        result = feedback(
            FeedbackInput(
                user_id="u1", message="stuck", category="bug", tool_name="gmail_send"
            )
        )
        assert result.forwarded_to == []
        with feedback_db() as s:
            row = s.get(AgentFeedback, result.id)
        assert row is not None
        assert (row.user_id, row.category, row.tool_name) == ("u1", "bug", "gmail_send")

    @pytest.mark.usefixtures("isolated_sinks")
    def test_failing_sink_does_not_fail_call(self, feedback_db):
        good, bad = _RecordingSink("good"), _RecordingSink("bad", fail=True)
        register_feedback_sink(bad)
        register_feedback_sink(good)
        result = feedback(FeedbackInput(user_id="u1", message="hi"))
        assert result.forwarded_to == ["good"]
        assert good.records[0].message == "hi"

    @pytest.mark.usefixtures("isolated_sinks")
    def test_sink_failure_logged_without_traceback(self, feedback_db):
        register_feedback_sink(_RecordingSink("bad", fail=True))
        with (
            patch.object(feedback_svc.log, "warning") as warn,
            patch.object(feedback_svc.log, "exception") as exc_log,
        ):
            feedback(FeedbackInput(user_id="u1", message="hi"))
        _fmt, name, _id, exc_type, exc = warn.call_args.args
        assert (name, exc_type, str(exc)) == ("bad", "RuntimeError", "sink down")
        exc_log.assert_not_called()

    @pytest.mark.usefixtures("isolated_sinks")
    def test_register_replaces_same_name(self):
        first, second = _RecordingSink("x"), _RecordingSink("x")
        register_feedback_sink(first)
        register_feedback_sink(second)
        assert feedback_svc._sinks == [second]

    @pytest.mark.usefixtures("isolated_sinks")
    def test_registered_sink_overrides_builtin(self):
        custom = _RecordingSink("slack")
        register_feedback_sink(custom)
        with patch.object(global_config, "FEEDBACK_SLACK_WEBHOOK_URL", _SLACK_URL):
            assert feedback_svc._active_sinks() == [custom]


class TestSlackSink(TestTemplate):
    @pytest.mark.usefixtures("isolated_sinks")
    def test_enabled_by_env_url(self, feedback_db, sink_http):
        with patch.object(global_config, "FEEDBACK_SLACK_WEBHOOK_URL", _SLACK_URL):
            result = feedback(FeedbackInput(user_id="u1", message="<!channel> help"))
        assert result.forwarded_to == ["slack"]
        (req,) = sink_http.requests
        assert str(req.url) == _SLACK_URL
        text = json.loads(req.content)["text"]
        assert "<!channel>" not in text
        assert "&lt;!channel&gt;" in text

    def test_http_error_raises_without_url(self, sink_http):
        sink_http.handler = lambda _r: httpx.Response(500)
        with pytest.raises(SinkError) as info:
            SlackWebhookSink(_SLACK_URL).send(_record())
        assert "hooks.slack.test" not in str(info.value)
        assert "500" in str(info.value)

    def test_transport_error_drops_httpx_frames(self, sink_http):
        def boom(_r: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out")

        sink_http.handler = boom
        with pytest.raises(SinkError) as info:
            SlackWebhookSink(_SLACK_URL).send(_record())
        assert info.value.__suppress_context__
        assert "hooks.slack.test" not in str(info.value)
        assert "hooks.slack.test" not in repr(SlackWebhookSink(_SLACK_URL))

    def test_escape(self):
        assert slack_escape("a & <b|c> <!here>") == "a &amp; &lt;b|c&gt; &lt;!here&gt;"


class TestFeedbackRegistration(TestTemplate):
    def test_registered_as_mutating_tool(self):
        from mcp_server.server import llm_tool_surface  # noqa: PLC0415

        entry = next(e for e in llm_tool_surface() if e.name == "feedback")
        assert entry.mutating is True

    def test_cli_categories_match_model(self):
        assert set(_CATEGORIES) == set(get_args(FeedbackCategory))


class TestFeedbackCli(TestTemplate):
    @pytest.mark.usefixtures("isolated_sinks")
    def test_message_argument(self, feedback_db):
        args = ["--format", "json", "feedback", "docs unclear", "-c", "confusing_docs"]
        res = runner.invoke(app, args)
        assert res.exit_code == 0, res.output
        assert json.loads(res.stdout)["forwarded_to"] == []

    @pytest.mark.usefixtures("isolated_sinks")
    def test_stdin(self, feedback_db):
        res = runner.invoke(app, ["feedback", "--stdin"], input="from a pipe\n")
        assert res.exit_code == 0, res.output
        with feedback_db() as s:
            assert s.query(AgentFeedback).one().message.strip() == "from a pipe"

    def test_dry_run_writes_nothing(self, feedback_db):
        res = runner.invoke(app, ["--dry-run", "feedback", "x"])
        assert res.exit_code == 0
        with feedback_db() as s:
            assert s.query(AgentFeedback).count() == 0

    def test_missing_message_fails_fast(self):
        res = runner.invoke(app, ["feedback"], input="")
        assert res.exit_code == 1

    def test_bad_category_fails(self):
        res = runner.invoke(app, ["feedback", "x", "-c", "nope"])
        assert res.exit_code == 1
