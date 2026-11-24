"""Agent feedback service - store, then fan out to pluggable sinks.

An agent that gets stuck calls ``feedback`` instead of giving up silently. The
submission is written to the ``agent_feedback`` table first (the durable
record), then offered to every sink on a best-effort basis: a sink that is
down or misconfigured is logged and skipped, and never fails the agent's call.

Two sinks ship built in (``services/feedback_sinks/``), each enabled by its
settings:

- Slack: ``FEEDBACK_SLACK_WEBHOOK_URL`` in ``.env``.
- Linear: ``FEEDBACK_LINEAR_API_KEY`` in ``.env`` plus
  ``feedback.linear.team_id`` in YAML. One issue per category + tool; repeats
  become comments.

To add another (Jira, email), implement ``FeedbackSink`` and register it at
import time; a registered sink named ``slack`` or ``linear`` replaces the
built-in one::

    from services.feedback_sinks import FeedbackRecord, SinkClient

    class JiraSink:
        name = "jira"

        def send(self, record: FeedbackRecord) -> None:
            with SinkClient() as client:  # raises SinkError on failure
                client.post_json(JIRA_URL, {...}, headers={...})

    register_feedback_sink(JiraSink())

Latency: the fan-out runs inline, after the row is committed, so the result
can report ``forwarded_to``. Each sink gets its own total budget of
``feedback.sink_timeout_seconds`` (enforced by ``SinkClient``), so the call
takes at most about one budget per enabled sink.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from loguru import logger as log

from db.engine import use_db_session
from db.models.feedback import AgentFeedback
from models.feedback import FeedbackInput, FeedbackResult
from services import service
from services.events import emit
from services.events.catalog import FEEDBACK_RECEIVED
from services.feedback_sinks import BUILTINS, FeedbackRecord, FeedbackSink

_sinks: list[FeedbackSink] = []


def register_feedback_sink(sink: FeedbackSink) -> None:
    """Add a sink to the fan-out. Re-registering the same name replaces it."""
    _sinks[:] = [s for s in _sinks if s.name != sink.name]
    _sinks.append(sink)


def _active_sinks() -> list[FeedbackSink]:
    sinks = list(_sinks)
    taken = {s.name for s in sinks}
    for name, factory in BUILTINS:
        if name in taken:
            continue
        sink = factory()
        if sink is not None:
            sinks.append(sink)
    return sinks


def _fan_out(record: FeedbackRecord) -> list[str]:
    delivered: list[str] = []
    for sink in _active_sinks():
        try:
            sink.send(record)
        except Exception as exc:  # noqa: BLE001
            # Defensive boundary: sinks are third-party/user-supplied adapters
            # and the row is already stored, so a failing sink must not fail
            # the agent's call. Log type + message only: a traceback would
            # print frame locals (loguru diagnose), which can hold secrets.
            log.warning(
                "feedback sink {} failed for {}: {}: {}",
                sink.name,
                record.id,
                type(exc).__name__,
                exc,
            )
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
