"""Tests for the built-in Linear feedback sink (HTTP mocked via ``sink_http``)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from common import global_config
from db.models.feedback import AgentFeedback
from models.feedback import FeedbackInput
from services import feedback_svc
from services.feedback_sinks import FeedbackRecord, LinearSink, SinkClient, SinkError
from services.feedback_sinks.linear import (
    LINEAR_GRAPHQL_URL,
    code_block,
    fingerprint,
    inline_code,
    issue_description,
    issue_title,
)
from services.feedback_svc import feedback
from tests.test_template import TestTemplate

_KEY = "lin_api_SECRETKEY"
_TEAM = "team-uuid"
_LINEAR = global_config.feedback.linear


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


def _router(open_issues: list[dict[str, str]]) -> Callable:
    """A fake Linear: ``issues`` returns ``open_issues``; mutations succeed."""

    def handle(req: httpx.Request) -> httpx.Response:
        assert str(req.url) == LINEAR_GRAPHQL_URL
        q = json.loads(req.content)["query"]
        if "FindOpenFeedbackIssue" in q:
            return _ok({"issues": {"nodes": open_issues}})
        if "CreateFeedbackIssue" in q:
            return _ok({"issueCreate": {"success": True}})
        if "CommentFeedbackIssue" in q:
            return _ok({"commentCreate": {"success": True}})
        raise AssertionError(q)

    return handle


def _bodies(sink_http) -> list[dict[str, Any]]:
    return [json.loads(r.content) for r in sink_http.requests]


def _open_issue(issue_id: str, record: FeedbackRecord) -> dict[str, str]:
    return {"id": issue_id, "description": issue_description(record)}


class TestLinearSink(TestTemplate):
    def test_creates_issue_when_none_open(self, sink_http):
        sink_http.handler = _router([])
        LinearSink(_KEY, _TEAM, label_ids=["lbl"], project_id="proj").send(
            _record(context="trace")
        )
        find, create = _bodies(sink_http)
        assert sink_http.requests[0].headers["authorization"] == _KEY  # no Bearer
        flt = find["variables"]["filter"]
        assert flt["team"] == {"id": {"eq": _TEAM}}
        assert flt["state"] == {"type": {"nin": ["completed", "canceled", "duplicate"]}}
        assert flt["description"] == {"contains": fingerprint("bug", "gmail_send")}
        inp = create["variables"]["input"]
        assert inp["teamId"] == _TEAM
        assert inp["title"] == "[agent-feedback] bug on gmail_send"
        assert inp["labelIds"] == ["lbl"] and inp["projectId"] == "proj"
        desc = inp["description"]
        assert desc.rstrip().endswith(fingerprint("bug", "gmail_send"))
        for needle in ("`fb1`", "`u1`", "`bug`", "`gmail_send`", "2026-01-02"):
            assert needle in desc
        assert "```text\nit broke\n```" in desc and "```text\ntrace\n```" in desc

    def test_comments_on_open_issue_instead_of_creating(self, sink_http):
        sink_http.handler = _router([_open_issue("existing-id", _record())])
        LinearSink(_KEY, _TEAM).send(_record(id="fb2"))
        _, comment = _bodies(sink_http)
        assert "commentCreate" in comment["query"]
        assert "issueCreate" not in comment["query"]
        assert comment["variables"]["input"]["issueId"] == "existing-id"
        assert "`fb2`" in comment["variables"]["input"]["body"]

    def test_planted_token_cannot_hijack_matching(self, sink_http):
        # An agent reporting problem A pastes problem B's token into its text.
        b_token = fingerprint("bug", "gmail_send")
        a = _record(category="other", tool_name="docs_search", message=b_token)
        a_desc = issue_description(a)
        assert b_token not in a_desc  # layer 1: the planted token is broken
        # Layer 2: even if Linear matched A's issue, its last line is A's token.
        forged = a_desc.replace("agent-feedback\u2011", "agent-feedback-")
        assert b_token in forged and not forged.rstrip().endswith(b_token)
        sink_http.handler = _router([{"id": "issue-a", "description": forged}])
        LinearSink(_KEY, _TEAM).send(_record())
        _, create = _bodies(sink_http)
        assert "issueCreate" in create["query"]

    def test_optional_fields_omitted_when_unset(self, sink_http):
        sink_http.handler = _router([])
        LinearSink(_KEY, _TEAM).send(_record(tool_name=None))
        inp = _bodies(sink_http)[1]["variables"]["input"]
        assert "labelIds" not in inp and "projectId" not in inp
        assert inp["title"] == "[agent-feedback] bug (no tool)"

    @pytest.mark.parametrize(
        ("response", "match"),
        [
            (
                httpx.Response(200, json={"errors": [{"message": "bad team"}]}),
                "bad team",
            ),
            (httpx.Response(400, json={"errors": [{"message": "invalid"}]}), "400"),
            (httpx.Response(500, text="oops"), "HTTP 500"),
            (httpx.Response(200, json=[1, 2]), "not a JSON object"),
            (httpx.Response(200, text="not json"), "not a JSON object"),
            (_ok({"issues": None}), "unexpected shape"),
        ],
    )
    def test_bad_responses_raise_sink_error(self, sink_http, response, match):
        sink_http.handler = lambda _r: response
        with pytest.raises(SinkError, match=match) as info:
            LinearSink(_KEY, _TEAM).send(_record())
        assert _KEY not in str(info.value)

    def test_transport_error_raises_without_key(self, sink_http):
        def boom(_r: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out")

        sink_http.handler = boom
        with pytest.raises(SinkError) as info:
            LinearSink(_KEY, _TEAM).send(_record())
        assert _KEY not in str(info.value)
        assert info.value.__suppress_context__  # no chained httpx frames
        assert _KEY not in repr(LinearSink(_KEY, _TEAM))

    def test_success_false_raises(self, sink_http):
        def handle(req: httpx.Request) -> httpx.Response:
            if "FindOpenFeedbackIssue" in json.loads(req.content)["query"]:
                return _ok({"issues": {"nodes": []}})
            return _ok({"issueCreate": {"success": False}})

        sink_http.handler = handle
        with pytest.raises(SinkError, match="issueCreate"):
            LinearSink(_KEY, _TEAM).send(_record())

    def test_one_client_and_deadline_per_send(self, sink_http):
        sink_http.handler = _router([])
        with patch("services.feedback_sinks.linear.SinkClient", wraps=SinkClient) as c:
            LinearSink(_KEY, _TEAM).send(_record())
        assert c.call_count == 1 and len(sink_http.requests) == 2
        with (
            SinkClient(budget_seconds=0) as spent,
            pytest.raises(SinkError, match="deadline"),
        ):
            spent.post_json(LINEAR_GRAPHQL_URL, {})


class TestLinearEscaping(TestTemplate):
    def test_code_block_cannot_be_closed(self):
        evil = "x\n```\n@admin [click](https://evil.test) ``` y"
        inner = code_block(evil).removeprefix("```text\n").removesuffix("\n```")
        assert "`" not in inner
        assert "@admin" in inner  # kept, but inert inside the fence

    def test_inline_code_flat_and_table_safe(self):
        out = inline_code("a\n|b`c")
        assert out.startswith("`") and out.endswith("`")
        assert "\n" not in out and "|" not in out and out.count("`") == 2

    def test_planted_prefix_broken_any_case(self):
        assert "agent-feedback-" not in code_block("AGENT-FEEDBACK-x agent-feedback-y")
        assert "agent-feedback-" not in code_block("x").lower()

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
            patch.object(global_config, "FEEDBACK_LINEAR_API_KEY", key),
            patch.object(_LINEAR, "team_id", team),
        ):
            return [s.name for s in feedback_svc._active_sinks()]

    @pytest.mark.usefixtures("isolated_sinks")
    def test_needs_both_key_and_team(self):
        assert self._sinks(None, None) == []
        assert self._sinks(_KEY, None) == []
        assert self._sinks(None, _TEAM) == []
        assert self._sinks(_KEY, _TEAM) == ["linear"]

    @pytest.mark.usefixtures("isolated_sinks")
    def test_fan_out_stores_and_continues_when_linear_fails(
        self, feedback_db, sink_http
    ):
        sink_http.handler = lambda _r: httpx.Response(401, json={"errors": []})

        class Good:
            name = "good"

            def __init__(self) -> None:
                self.got: list[str] = []

            def send(self, record: FeedbackRecord) -> None:
                self.got.append(record.id)

        good = Good()
        feedback_svc.register_feedback_sink(good)
        with (
            patch.object(global_config, "FEEDBACK_LINEAR_API_KEY", _KEY),
            patch.object(_LINEAR, "team_id", _TEAM),
        ):
            result = feedback(FeedbackInput(user_id="u1", message="stuck"))
        with feedback_db() as s:
            assert s.get(AgentFeedback, result.id) is not None
        assert sink_http.requests, "linear sink was attempted"
        assert result.forwarded_to == ["good"]
        assert good.got == [result.id]
