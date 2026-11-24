"""Tests for the built-in Linear feedback sink (HTTP mocked, no network)."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from common import global_config
from db.models.feedback import AgentFeedback
from models.feedback import FeedbackInput
from services import feedback_linear, feedback_svc
from services.feedback_linear import (
    LINEAR_GRAPHQL_URL,
    LinearSink,
    LinearSinkError,
    code_block,
    fingerprint,
    inline_code,
    issue_title,
)
from services.feedback_svc import FeedbackRecord, feedback
from tests.test_feedback import _isolated_sinks, _patch_db
from tests.test_template import TestTemplate

_KEY = "lin_api_SECRETKEY"
_TEAM = "team-uuid"

Handler = Callable[[dict[str, Any]], httpx.Response]


def _record(**kw: Any) -> FeedbackRecord:
    base: dict[str, Any] = {
        "id": "fb1",
        "user_id": "u1",
        "category": "bug",
        "message": "it broke",
        "tool_name": "gmail_send",
        "context": None,
        "created_at": datetime(2026, 1, 2, tzinfo=UTC),
    }
    base.update(kw)
    return FeedbackRecord(**base)


def _ok(data: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"data": data})


@contextmanager
def _linear(handler: Handler):
    """Route ``httpx.post`` in the sink through ``handler``; yield the calls."""
    calls: list[dict[str, Any]] = []

    def fake_post(url, json, headers, timeout):  # noqa: A002 - mirrors httpx.post
        assert url == LINEAR_GRAPHQL_URL
        assert timeout == global_config.feedback.sink_timeout_seconds
        calls.append({"json": json, "headers": headers})
        resp = handler(json)
        resp.request = httpx.Request("POST", url)
        return resp

    with patch("services.feedback_linear.httpx.post", fake_post):
        yield calls


def _router(existing: str | None) -> Handler:
    def handle(body: dict[str, Any]) -> httpx.Response:
        q = body["query"]
        if "FindOpenFeedbackIssue" in q:
            nodes = [{"id": existing, "identifier": "ENG-1", "url": "u"}]
            return _ok({"issues": {"nodes": nodes if existing else []}})
        if "CreateFeedbackIssue" in q:
            issue = {"id": "new", "identifier": "ENG-2", "url": "u"}
            return _ok({"issueCreate": {"success": True, "issue": issue}})
        if "CommentFeedbackIssue" in q:
            return _ok({"commentCreate": {"success": True, "comment": {"id": "c"}}})
        raise AssertionError(q)

    return handle


class TestLinearSink(TestTemplate):
    def test_creates_issue_when_none_open(self):
        sink = LinearSink(_KEY, _TEAM, label_ids=["lbl"], project_id="proj")
        with _linear(_router(None)) as calls:
            sink.send(_record(context="trace"))
        find, create = calls
        assert find["headers"] == {"Authorization": _KEY}  # raw key, no Bearer
        flt = find["json"]["variables"]["filter"]
        assert flt["team"] == {"id": {"eq": _TEAM}}
        assert flt["state"] == {"type": {"nin": ["completed", "canceled", "duplicate"]}}
        assert flt["description"] == {"contains": fingerprint("bug", "gmail_send")}
        inp = create["json"]["variables"]["input"]
        assert inp["teamId"] == _TEAM
        assert inp["title"] == "[agent-feedback] bug on gmail_send"
        assert inp["labelIds"] == ["lbl"] and inp["projectId"] == "proj"
        desc = inp["description"]
        assert desc.rstrip().endswith(fingerprint("bug", "gmail_send"))
        for needle in ("`fb1`", "`u1`", "`bug`", "`gmail_send`", "2026-01-02"):
            assert needle in desc
        assert "```text\nit broke\n```" in desc and "```text\ntrace\n```" in desc

    def test_comments_on_open_issue_instead_of_creating(self):
        with _linear(_router("existing-id")) as calls:
            LinearSink(_KEY, _TEAM).send(_record(id="fb2"))
        assert len(calls) == 2
        q = calls[1]["json"]
        assert "commentCreate" in q["query"] and "issueCreate" not in q["query"]
        assert q["variables"]["input"]["issueId"] == "existing-id"
        assert "`fb2`" in q["variables"]["input"]["body"]

    def test_optional_fields_omitted_when_unset(self):
        with _linear(_router(None)) as calls:
            LinearSink(_KEY, _TEAM).send(_record(tool_name=None))
        inp = calls[1]["json"]["variables"]["input"]
        assert "labelIds" not in inp and "projectId" not in inp
        assert inp["title"] == "[agent-feedback] bug (no tool)"

    def test_graphql_errors_raise(self):
        def handle(_body: dict[str, Any]) -> httpx.Response:
            return httpx.Response(200, json={"errors": [{"message": "bad team"}]})

        with _linear(handle), pytest.raises(LinearSinkError, match="bad team"):
            LinearSink(_KEY, _TEAM).send(_record())

    def test_http_error_and_transport_error_raise_without_key(self):
        def http_500(_body: dict[str, Any]) -> httpx.Response:
            return httpx.Response(500, text="oops")

        def boom(_body: dict[str, Any]) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out")

        for handler in (http_500, boom):
            with _linear(handler), pytest.raises(LinearSinkError) as info:
                LinearSink(_KEY, _TEAM).send(_record())
            assert _KEY not in str(info.value)
            assert info.value.__suppress_context__  # no chained httpx frames
        assert _KEY not in repr(LinearSink(_KEY, _TEAM))

    def test_success_false_raises(self):
        def handle(body: dict[str, Any]) -> httpx.Response:
            if "FindOpenFeedbackIssue" in body["query"]:
                return _ok({"issues": {"nodes": []}})
            return _ok({"issueCreate": {"success": False, "issue": None}})

        with _linear(handle), pytest.raises(LinearSinkError):
            LinearSink(_KEY, _TEAM).send(_record())


class TestLinearEscaping(TestTemplate):
    def test_code_block_cannot_be_closed(self):
        evil = "x\n```\n@admin [click](https://evil.test) ``` y"
        block = code_block(evil)
        inner = block.removeprefix("```text\n").removesuffix("\n```")
        assert "`" not in inner
        assert "@admin" in inner  # kept, but inert inside the fence

    def test_inline_code_flat_and_table_safe(self):
        out = inline_code("a\n|b`c")
        assert out.startswith("`") and out.endswith("`")
        assert "\n" not in out and "|" not in out and out.count("`") == 2

    def test_caps_length(self):
        assert len(code_block("a" * 10_000)) < 4100
        assert len(inline_code("b" * 1_000)) <= 130

    def test_title_sanitises_tool_name(self):
        title = issue_title("bug", "x](https://e.test) @here")
        assert title.startswith("[agent-feedback] bug on ")
        assert " " not in title.removeprefix("[agent-feedback] bug on ")
        assert "(" not in title and "@" not in title

    def test_fingerprint_is_per_category_and_tool(self):
        assert fingerprint("bug", "a") == fingerprint("bug", "a")
        assert fingerprint("bug", "a") != fingerprint("other", "a")
        assert fingerprint("bug", "a") != fingerprint("bug", None)
        assert fingerprint("bug", None).startswith("agent-feedback-")


class TestLinearEnablement(TestTemplate):
    def _sinks(self, key: str | None, team: str | None) -> list[str]:
        with (
            _isolated_sinks(),
            patch.object(global_config, "FEEDBACK_SLACK_WEBHOOK_URL", None),
            patch.object(global_config, "LINEAR_API_KEY", key),
            patch.object(global_config, "LINEAR_TEAM_ID", team),
        ):
            return [s.name for s in feedback_svc._active_sinks()]

    def test_needs_both_key_and_team(self):
        assert self._sinks(None, None) == []
        assert self._sinks(_KEY, None) == []
        assert self._sinks(None, _TEAM) == []
        assert self._sinks(_KEY, _TEAM) == ["linear"]

    def test_fan_out_stores_and_continues_when_linear_fails(self):
        def handle(_body: dict[str, Any]) -> httpx.Response:
            return httpx.Response(401, json={"errors": [{"message": "auth"}]})

        class Good:
            name = "good"
            got: list[str] = []

            def send(self, record: FeedbackRecord) -> None:
                self.got.append(record.id)

        with (
            _patch_db() as factory,
            _isolated_sinks(),
            patch.object(global_config, "FEEDBACK_SLACK_WEBHOOK_URL", None),
            patch.object(global_config, "LINEAR_API_KEY", _KEY),
            patch.object(global_config, "LINEAR_TEAM_ID", _TEAM),
            _linear(handle) as calls,
        ):
            feedback_svc.register_feedback_sink(Good())
            result = feedback(FeedbackInput(user_id="u1", message="stuck"))
            with factory() as s:
                assert s.get(AgentFeedback, result.id) is not None
        assert calls, "linear sink was attempted"
        assert result.forwarded_to == ["good"]
        assert Good.got == [result.id]

    def test_module_constant_is_linear_endpoint(self):
        assert feedback_linear.LINEAR_GRAPHQL_URL == "https://api.linear.app/graphql"
