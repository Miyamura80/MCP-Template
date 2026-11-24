"""Agent feedback service - store, then fan out to pluggable sinks.

An agent that gets stuck calls ``feedback`` instead of giving up silently. The
submission is written to the ``agent_feedback`` table first (the durable
record), then offered to every registered :class:`FeedbackSink` on a
best-effort basis: a sink that is down or misconfigured is logged and skipped,
and never fails the agent's call.

One sink ships built in - Slack, via an incoming-webhook URL in
``FEEDBACK_SLACK_WEBHOOK_URL``. To add another (Linear, Jira, email), implement
the protocol and register it at import time::

    class LinearSink:
        name = "linear"

        def send(self, record: FeedbackRecord) -> None:
            ...  # POST to Linear's API; raise on failure

    register_feedback_sink(LinearSink())
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx
from loguru import logger as log

from common import global_config
from db.engine import use_db_session
from db.models.feedback import AgentFeedback
from models.feedback import FeedbackInput, FeedbackResult
from services import service
from services.events import emit
from services.events.catalog import FEEDBACK_RECEIVED


@dataclass(frozen=True)
class FeedbackRecord:
    """The stored submission, as handed to sinks."""

    id: str
    user_id: str
    category: str
    message: str
    tool_name: str | None
    context: str | None
    created_at: datetime


class FeedbackSink(Protocol):
    """An outbound destination for feedback. ``send`` raises on failure."""

    name: str

    def send(self, record: FeedbackRecord) -> None: ...


_sinks: list[FeedbackSink] = []


def register_feedback_sink(sink: FeedbackSink) -> None:
    """Add a sink to the fan-out. Re-registering the same name replaces it."""
    _sinks[:] = [s for s in _sinks if s.name != sink.name]
    _sinks.append(sink)


def _active_sinks() -> list[FeedbackSink]:
    sinks = list(_sinks)
    url = global_config.FEEDBACK_SLACK_WEBHOOK_URL
    if url and not any(s.name == "slack" for s in sinks):
        sinks.append(SlackWebhookSink(url))
    return sinks


# ---------------------------------------------------------------------------
# Built-in Slack sink
# ---------------------------------------------------------------------------


def _slack_escape(text: str) -> str:
    """Escape Slack mrkdwn control characters.

    The message is agent-written, so treat it as untrusted: escaping ``&``,
    ``<`` and ``>`` (Slack's documented set) neutralises ``<!channel>`` /
    ``<!here>`` pings and disguised ``<url|label>`` links.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class SlackWebhookSink:
    """Post feedback to a Slack incoming webhook."""

    name = "slack"

    def __init__(self, webhook_url: str) -> None:
        self._url = webhook_url

    def send(self, record: FeedbackRecord) -> None:
        lines = [
            f"*Agent feedback* `{record.category}`"
            + (f" on `{_slack_escape(record.tool_name)}`" if record.tool_name else ""),
            _slack_escape(record.message),
        ]
        if record.context:
            lines.append(f"```{_slack_escape(record.context)}```")
        lines.append(
            f"user `{_slack_escape(record.user_id or 'anonymous')}` · id `{record.id}`"
        )
        resp = httpx.post(
            self._url,
            json={"text": "\n".join(lines)},
            timeout=global_config.feedback.sink_timeout_seconds,
        )
        resp.raise_for_status()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _fan_out(record: FeedbackRecord) -> list[str]:
    delivered: list[str] = []
    for sink in _active_sinks():
        try:
            sink.send(record)
        except Exception:  # noqa: BLE001
            # Defensive boundary: sinks are third-party/user-supplied adapters
            # and the row is already stored, so a failing sink must not fail
            # the agent's call.
            log.exception("feedback sink {} failed for {}", sink.name, record.id)
            continue
        delivered.append(sink.name)
    return delivered


@service(
    name="feedback",
    description=(
        "Report a problem with this server so its developers can fix it. Call "
        "this when you get stuck: a tool errors in a way you cannot recover "
        "from, a description or doc misled you, or a capability you need is "
        "missing. Describe what you tried, what happened and what you expected; "
        "never include secrets or the user's private data."
    ),
    input_model=FeedbackInput,
    output_model=FeedbackResult,
    mutating=True,
)
def feedback(input: FeedbackInput) -> FeedbackResult:
    record = FeedbackRecord(
        id=secrets.token_hex(16),
        user_id=input.user_id,
        category=input.category,
        message=input.message,
        tool_name=input.tool_name,
        context=input.context,
        created_at=datetime.now(UTC),
    )
    with use_db_session() as session:
        session.add(
            AgentFeedback(
                id=record.id,
                user_id=record.user_id,
                category=record.category,
                message=record.message,
                tool_name=record.tool_name,
                context=record.context,
                created_at=record.created_at,
            )
        )
        if record.user_id:  # the CLI has no principal to deliver to
            emit(
                record.user_id,
                FEEDBACK_RECEIVED,
                {
                    "id": record.id,
                    "category": record.category,
                    "toolName": record.tool_name,
                },
                session=session,
            )
        session.commit()
    return FeedbackResult(
        id=record.id,
        received_at=record.created_at,
        forwarded_to=_fan_out(record),
    )
