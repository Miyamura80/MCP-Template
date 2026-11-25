"""Built-in Linear sink for agent feedback: one issue per distinct problem.

Enabled when ``FEEDBACK_LINEAR_API_KEY`` (a personal API key, sent raw in the
``Authorization`` header, no ``Bearer``) and ``feedback.linear.team_id`` are
both set. ``feedback.linear.label_ids`` / ``project_id`` are optional extras
for new issues.

Dedupe. A problem's fingerprint is ``category`` + ``tool_name``, hashed into a
token like ``agent-feedback-3f2a9c0d1e4b5a6f`` that the sink writes as the last
line of the issue description. Before creating, it asks Linear for issues in
the team whose description contains the token and whose workflow state type is
not ``completed``, ``canceled`` or ``duplicate``, then keeps only an issue whose
*last line* is the token. A match gets a comment (``commentCreate``); no match
gets a new issue (``issueCreate``). The token is in the description, not the
title, so people can retitle issues freely.

The token is public (anyone can compute it), so agent text must not be able to
plant one in another issue and hijack its reports. Two layers stop that: every
``agent-feedback-`` in agent text has its hyphen swapped for a lookalike, so it
no longer contains a token, and only the final line counts, which is always
written by the sink. Two concurrent first reports of the same problem can still
both create an issue; that is rare and harmless, so there is no lock.

Agent-written text is untrusted. ``message`` and ``context`` go inside fenced
code blocks (literal text: no mentions, links or formatting) with every
backtick swapped for a lookalike, so the text cannot close the fence. Short
fields are flattened to one line, with pipes swapped so they cannot break the
table. The raw text is kept unmodified in the ``agent_feedback`` table.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from common import global_config
from services.feedback_sinks.base import FeedbackRecord, SinkClient, SinkError

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"

_MAX_BLOCK = 4000
_MAX_INLINE = 128
_CANDIDATES = 10
# WorkflowState.type values (schema: triage, backlog, unstarted, started,
# completed, canceled, duplicate); the last three mean the issue is closed.
_CLOSED_STATE_TYPES = ["completed", "canceled", "duplicate"]
_TOKEN_PREFIX = "agent-feedback-"
_PLANTED_PREFIX = re.compile(r"agent-feedback-", re.IGNORECASE)

_FIND_OPEN = """
query FindOpenFeedbackIssue($filter: IssueFilter, $first: Int) {
  issues(filter: $filter, first: $first) { nodes { id description } }
}
"""

_ISSUE_CREATE = """
mutation CreateFeedbackIssue($input: IssueCreateInput!) {
  issueCreate(input: $input) { success }
}
"""

_COMMENT_CREATE = """
mutation CommentFeedbackIssue($input: CommentCreateInput!) {
  commentCreate(input: $input) { success }
}
"""


def fingerprint(category: str, tool_name: str | None) -> str:
    """Stable per-problem token, e.g. ``agent-feedback-3f2a9c0d1e4b5a6f``."""
    raw = f"{category}\x00{tool_name or ''}".encode()
    return _TOKEN_PREFIX + hashlib.sha256(raw).hexdigest()[:16]


def _defang(text: str) -> str:
    # U+02CB MODIFIER LETTER GRAVE ACCENT looks like a backtick but is not
    # markdown syntax, so agent text can never close or open a code span or
    # fence. U+2011 NON-BREAKING HYPHEN breaks any planted fingerprint token.
    text = text.replace("`", "\u02cb")
    return _PLANTED_PREFIX.sub(lambda m: m.group(0)[:-1] + "\u2011", text)


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def code_block(text: str) -> str:
    """Untrusted multi-line text as an inert fenced block."""
    return "```text\n" + _cap(_defang(text), _MAX_BLOCK) + "\n```"


def inline_code(text: str) -> str:
    """Untrusted short text as one-line inline code, safe in a table cell."""
    flat = " ".join(text.split()).replace("|", "\u00a6") or "-"
    return "`" + _cap(_defang(flat), _MAX_INLINE) + "`"


def issue_title(category: str, tool_name: str | None) -> str:
    # Titles are plain text in Linear; keep only identifier-ish characters so
    # the title stays a readable, predictable label.
    if not tool_name:
        return f"[agent-feedback] {category} (no tool)"
    tool = re.sub(r"[^A-Za-z0-9_.:/-]", "_", tool_name)[:_MAX_INLINE]
    return f"[agent-feedback] {category} on {tool}"


def _report(record: FeedbackRecord) -> str:
    rows = [
        ("Feedback id", record.id),
        ("User", record.user_id or "anonymous"),
        ("Category", record.category),
        ("Tool", record.tool_name or "-"),
        ("Created", record.created_at.isoformat() if record.created_at else "-"),
    ]
    parts = [
        "| Field | Value |",
        "|---|---|",
        *(f"| {k} | {inline_code(v)} |" for k, v in rows),
        "",
        "**Message** (agent-written, untrusted)",
        "",
        code_block(record.message),
    ]
    if record.context:
        parts += ["", "**Context**", "", code_block(record.context)]
    return "\n".join(parts)


def issue_description(record: FeedbackRecord) -> str:
    intro = (
        "Reported by an agent through the `feedback` tool. Later reports of the"
        " same category and tool are added as comments."
    )
    token = fingerprint(record.category, record.tool_name)
    return "\n".join([intro, "", _report(record), "", "---", token])


def _ends_with_token(description: object, token: str) -> bool:
    if not isinstance(description, str):
        return False
    lines = [ln.strip() for ln in description.strip().splitlines()]
    return bool(lines) and lines[-1] == token


class LinearSink:
    """Create or comment on a Linear issue per distinct agent problem."""

    name = "linear"

    def __init__(
        self,
        api_key: str,
        team_id: str,
        *,
        label_ids: list[str] | None = None,
        project_id: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._team_id = team_id
        self._label_ids = list(label_ids or [])
        self._project_id = project_id

    def __repr__(self) -> str:  # keep the key out of any repr
        return f"LinearSink(team_id={self._team_id!r})"

    def _graphql(
        self, client: SinkClient, query: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        body = client.post_json(
            LINEAR_GRAPHQL_URL,
            {"query": query, "variables": variables},
            headers={"Authorization": self._api_key},
        )
        if not isinstance(body, dict):
            raise SinkError("Linear response was not a JSON object")
        errors = body.get("errors")
        if errors:
            msgs = "; ".join(
                str(e.get("message", "?"))
                for e in (errors if isinstance(errors, list) else [])
                if isinstance(e, dict)
            )
            raise SinkError(f"Linear GraphQL error: {msgs or 'no detail'}")
        data = body.get("data")
        if not isinstance(data, dict):
            raise SinkError("Linear response had no data")
        return data

    def _find_open_issue(self, client: SinkClient, token: str) -> str | None:
        flt = {
            "team": {"id": {"eq": self._team_id}},
            "state": {"type": {"nin": _CLOSED_STATE_TYPES}},
            "description": {"contains": token},
        }
        data = self._graphql(client, _FIND_OPEN, {"filter": flt, "first": _CANDIDATES})
        try:
            nodes = data["issues"]["nodes"]
        except (KeyError, TypeError):
            raise SinkError("Linear issues response had an unexpected shape") from None
        for node in nodes or []:
            if isinstance(node, dict) and _ends_with_token(
                node.get("description"), token
            ):
                return str(node["id"])
        return None

    def _mutate(self, client: SinkClient, query: str, field: str, inp: dict) -> None:
        data = self._graphql(client, query, {"input": inp})
        result = data.get(field)
        if not (isinstance(result, dict) and result.get("success") is True):
            raise SinkError(f"Linear {field} did not succeed")

    def send(self, record: FeedbackRecord) -> None:
        token = fingerprint(record.category, record.tool_name)
        with SinkClient() as client:
            issue_id = self._find_open_issue(client, token)
            if issue_id:
                body = "Another agent report of this problem.\n\n" + _report(record)
                inp = {"issueId": issue_id, "body": body}
                self._mutate(client, _COMMENT_CREATE, "commentCreate", inp)
                return
            issue: dict[str, Any] = {
                "teamId": self._team_id,
                "title": issue_title(record.category, record.tool_name),
                "description": issue_description(record),
            }
            if self._label_ids:
                issue["labelIds"] = self._label_ids
            if self._project_id:
                issue["projectId"] = self._project_id
            self._mutate(client, _ISSUE_CREATE, "issueCreate", issue)


def linear_sink_from_config() -> LinearSink | None:
    """The built-in sink when the key and ``feedback.linear.team_id`` are set."""
    key = global_config.FEEDBACK_LINEAR_API_KEY
    cfg = global_config.feedback.linear
    if not (key and cfg.team_id):
        return None
    return LinearSink(
        key, cfg.team_id, label_ids=cfg.label_ids, project_id=cfg.project_id
    )
