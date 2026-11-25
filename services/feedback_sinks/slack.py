"""Built-in Slack sink: post each submission to an incoming webhook.

Enabled when ``FEEDBACK_SLACK_WEBHOOK_URL`` is set. The webhook URL is the
secret, so it only ever travels through :class:`SinkClient`, which never puts
it in an error message, and ``__repr__`` leaves it out.
"""

from __future__ import annotations

from common import global_config
from services.feedback_sinks.base import FeedbackRecord, SinkClient


def slack_escape(text: str) -> str:
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

    def __repr__(self) -> str:  # the webhook URL is the secret
        return "SlackWebhookSink()"

    def send(self, record: FeedbackRecord) -> None:
        lines = [
            f"*Agent feedback* `{record.category}`"
            + (f" on `{slack_escape(record.tool_name)}`" if record.tool_name else ""),
            slack_escape(record.message),
        ]
        if record.context:
            lines.append(f"```{slack_escape(record.context)}```")
        lines.append(
            f"user `{slack_escape(record.user_id or 'anonymous')}` · id `{record.id}`"
        )
        with SinkClient() as client:
            client.post_json(self._url, {"text": "\n".join(lines)})


def slack_sink_from_config() -> SlackWebhookSink | None:
    """The built-in sink when ``FEEDBACK_SLACK_WEBHOOK_URL`` is set."""
    url = global_config.FEEDBACK_SLACK_WEBHOOK_URL
    return SlackWebhookSink(url) if url else None
