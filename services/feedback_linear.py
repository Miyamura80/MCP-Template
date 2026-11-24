"""Built-in Linear sink for agent feedback: one issue per distinct problem.

Enabled when both ``LINEAR_API_KEY`` (a personal API key, sent raw in the
``Authorization`` header, no ``Bearer``) and ``LINEAR_TEAM_ID`` are set.

Dedupe. A problem's fingerprint is ``category`` + ``tool_name``, hashed into a
token like ``agent-feedback-3f2a9c0d1e4b5a6f`` that is written as the last
line of the issue description. Before creating, the sink asks Linear for an
issue in the team whose description contains that token and whose workflow
state is not ``completed``/``canceled``/``duplicate``. A hit gets a comment
(``commentCreate``); a miss gets a new issue (``issueCreate``). The token lives
in the description, not the title, so a human can retitle the issue freely,
and it is plain hex, so neither markdown rendering nor agent text can forge or
break it. Two concurrent first reports of the same problem can still both
create an issue; that is rare and harmless, so there is no lock.

Agent-written text is untrusted. ``message`` and ``context`` go inside fenced
code blocks (literal text: no mentions, links or formatting) with every
backtick swapped for a lookalike, so the text cannot close the fence. Short
fields are flattened to one line (pipes swapped too, so they cannot break the
table) and get the same treatment. The raw text is
kept unmodified in the ``agent_feedback`` table.

Failure is loud for the fan-out (which logs and skips) but never leaks the
key: errors are re-raised as :class:`LinearSinkError` ``from None`` so the
logged traceback (loguru runs with ``diagnose=True``) carries no frame whose
locals hold the request headers.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Any

import httpx

from common import global_config

if TYPE_CHECKING:
    from services.feedback_svc import FeedbackRecord

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"

_MAX_BLOCK = 4000
_MAX_INLINE = 128
_CLOSED_STATE_TYPES = ["completed", "canceled", "duplicate"]

_FIND_OPEN = """
query FindOpenFeedbackIssue($filter: IssueFilter) {
  issues(filter: $filter, first: 1) { nodes { id identifier url } }
}
"""

_ISSUE_CREATE = """
mutation CreateFeedbackIssue($input: IssueCreateInput!) {
  issueCreate(input: $input) { success issue { id identifier url } }
}
"""

_COMMENT_CREATE = """
mutation CommentFeedbackIssue($input: CommentCreateInput!) {
  commentCreate(input: $input) { success comment { id } }
}
"""


class LinearSinkError(RuntimeError):
    """Linear rejected or failed a request. Never carries the API key."""


def fingerprint(category: str, tool_name: str | None) -> str:
    """Stable per-problem token, e.g. ``agent-feedback-3f2a9c0d1e4b5a6f``."""
    raw = f"{category}\x00{tool_name or ''}".encode()
    return "agent-feedback-" + hashlib.sha256(raw).hexdigest()[:16]


def _defang(text: str) -> str:
    # U+02CB MODIFIER LETTER GRAVE ACCENT looks like a backtick but is not
    # markdown syntax, so agent text can never close or open a code span/fence.
    return text.replace("`", "ˋ")


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


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
    return "\n".join(
        [
            "Reported by an agent through the `feedback` tool. Later reports of"
            " the same category and tool are added as comments.",
            "",
            _report(record),
            "",
            "---",
            fingerprint(record.category, record.tool_name),
        ]
    )


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

    def __repr__(self) -> str:  # keep the key out of any repr / traceback
        return f"LinearSink(team_id={self._team_id!r})"

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = httpx.post(
                LINEAR_GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers={"Authorization": self._api_key},
                timeout=global_config.feedback.sink_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            err = f"Linear request failed: {type(exc).__name__}"
            raise LinearSinkError(err) from None
        try:
            body = resp.json()
        except ValueError:
            body = {}
        errors = body.get("errors") if isinstance(body, dict) else None
        if resp.status_code != 200 or errors:
            msgs = "; ".join(
                str(e.get("message", "?")) for e in errors or [] if isinstance(e, dict)
            )
            err = f"Linear HTTP {resp.status_code}: {msgs or 'no error detail'}"
            raise LinearSinkError(err) from None
        data = body.get("data")
        if not isinstance(data, dict):
            raise LinearSinkError("Linear response had no data") from None
        return data

    def _find_open_issue(self, token: str) -> str | None:
        flt = {
            "team": {"id": {"eq": self._team_id}},
            "state": {"type": {"nin": _CLOSED_STATE_TYPES}},
            "description": {"contains": token},
        }
        nodes = self._graphql(_FIND_OPEN, {"filter": flt})["issues"]["nodes"]
        return nodes[0]["id"] if nodes else None

    def send(self, record: FeedbackRecord) -> None:
        token = fingerprint(record.category, record.tool_name)
        issue_id = self._find_open_issue(token)
        if issue_id:
            body = "Another agent report of this problem.\n\n" + _report(record)
            data = self._graphql(
                _COMMENT_CREATE, {"input": {"issueId": issue_id, "body": body}}
            )
            ok = data["commentCreate"]["success"]
        else:
            issue: dict[str, Any] = {
                "teamId": self._team_id,
                "title": issue_title(record.category, record.tool_name),
                "description": issue_description(record),
            }
            if self._label_ids:
                issue["labelIds"] = self._label_ids
            if self._project_id:
                issue["projectId"] = self._project_id
            data = self._graphql(_ISSUE_CREATE, {"input": issue})
            ok = data["issueCreate"]["success"]
        if not ok:
            raise LinearSinkError("Linear reported success=false")


def linear_sink_from_config() -> LinearSink | None:
    """The built-in sink when ``LINEAR_API_KEY`` and ``LINEAR_TEAM_ID`` are set."""
    key, team = global_config.LINEAR_API_KEY, global_config.LINEAR_TEAM_ID
    if not (key and team):
        return None
    cfg = global_config.feedback
    return LinearSink(
        key, team, label_ids=cfg.linear_label_ids, project_id=cfg.linear_project_id
    )
