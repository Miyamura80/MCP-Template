"""Shared plumbing for feedback sinks: the record, the protocol, safe HTTP.

A sink's ``send`` raises :class:`SinkError` (or anything else) on failure; the
fan-out in ``services/feedback_svc.py`` logs the exception type and message,
never a traceback, so frame locals (URLs, headers, keys) cannot reach the log.

:class:`SinkClient` is the one HTTP path for built-in sinks and is meant for
custom ones too. It holds a single ``httpx.Client`` for one ``send`` call and a
total deadline (``feedback.sink_timeout_seconds``): every request gets only the
time that is left, and once it is spent the next request fails fast. Transport
and HTTP errors are re-raised as a ``SinkError`` whose message names the
failure but never the URL or headers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol, Self

import httpx

from common import global_config

_MAX_DETAIL = 300


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


class SinkError(RuntimeError):
    """A sink failed. The message is safe to log: no URLs, headers or keys."""


def _error_detail(resp: httpx.Response) -> str:
    """GraphQL-style ``errors[].message`` from a failed response, if any."""
    try:
        body = resp.json()
    except ValueError:
        return ""
    errors = body.get("errors") if isinstance(body, dict) else None
    if not isinstance(errors, list):
        return ""
    msgs = "; ".join(str(e.get("message", "?")) for e in errors if isinstance(e, dict))
    return msgs[:_MAX_DETAIL]


def _new_client() -> httpx.Client:
    # Seam for tests (``tests/conftest.py:sink_http`` swaps the transport).
    return httpx.Client(follow_redirects=False)


class SinkClient:
    """One ``httpx.Client`` and one total deadline for a single ``send``.

    Use as a context manager; ``post_json`` is the only request method.
    """

    def __init__(self, budget_seconds: float | None = None) -> None:
        budget = (
            global_config.feedback.sink_timeout_seconds
            if budget_seconds is None
            else budget_seconds
        )
        self._deadline = time.monotonic() + budget
        self._client = _new_client()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._client.close()

    def __repr__(self) -> str:
        return "SinkClient()"

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> Any:
        """POST ``payload``; return the parsed JSON body (``None`` if not JSON).

        Raises :class:`SinkError` on a spent deadline, a transport error or a
        non-2xx status. ``httpx`` timeouts are per phase, so each phase is
        capped at the time left; a slow request can overrun the budget by a
        few phases, but never by another full request.
        """
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise SinkError("sink deadline exceeded")
        try:
            resp = self._client.post(
                url, json=payload, headers=headers, timeout=httpx.Timeout(left)
            )
        except httpx.HTTPError as exc:
            # ``from None`` drops the httpx frames, whose locals hold the URL
            # and headers; the message carries the error type only.
            raise SinkError(f"request failed: {type(exc).__name__}") from None
        if not resp.is_success:
            detail = _error_detail(resp)
            msg = f"HTTP {resp.status_code}" + (f": {detail}" if detail else "")
            raise SinkError(msg)
        try:
            return resp.json()
        except ValueError:
            return None
