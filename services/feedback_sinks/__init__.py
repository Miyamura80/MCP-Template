"""Outbound destinations for agent feedback.

``base`` holds the record, the :class:`FeedbackSink` protocol, :class:`SinkError`
and :class:`SinkClient` (the safe HTTP path). ``slack`` and ``linear`` are the
built-in sinks, each with a ``*_from_config`` factory that returns ``None``
when its settings are missing. ``BUILTINS`` lists those factories for the
fan-out in ``services/feedback_svc.py``.
"""

from collections.abc import Callable

from services.feedback_sinks.base import (
    FeedbackRecord,
    FeedbackSink,
    SinkClient,
    SinkError,
)
from services.feedback_sinks.linear import LinearSink, linear_sink_from_config
from services.feedback_sinks.slack import SlackWebhookSink, slack_sink_from_config

BUILTINS: tuple[tuple[str, Callable[[], FeedbackSink | None]], ...] = (
    ("slack", slack_sink_from_config),
    ("linear", linear_sink_from_config),
)

__all__ = [
    "BUILTINS",
    "FeedbackRecord",
    "FeedbackSink",
    "LinearSink",
    "SinkClient",
    "SinkError",
    "SlackWebhookSink",
]
