"""E2E-only fake Gmail backend.

Returns a stand-in for the ``googleapiclient`` Gmail v1 ``Resource`` that serves
canned, raw-Gmail-API-shaped fixtures instead of talking to Google. This exists
so the MCP-App e2e harness (``.agents/skills/goose-gui-e2e``) can render the
``gmail_get_thread`` reader iframe in a real host **without** a linked Google
account, an OAuth consent, or any network egress.

Design:
  * The fixtures are shaped like the real ``users().threads().get(format="full")``
    payload, so the *real* service code (``_parse_message_resource`` -> Pydantic
    ``GmailThread``) runs end-to-end. Only the network boundary is faked.
  * The fake ``Resource`` implements just the chained calls the Gmail services
    make (``users().threads().get()``, ``users().drafts().list()``, ...). Unknown
    methods raise ``NotImplementedError`` loudly rather than silently returning
    junk, so a new code path that reaches Google in a faked run fails visibly.

This module must NEVER be reachable in production: the single entry point
(``build_fake_gmail_client``) is only imported by ``services.gmail_svc`` when
``GMAIL_FAKE_BACKEND=1`` **and** ``DEV_ENV != prod`` (that guard lives at the
call site so this module has no way to activate itself).
"""

from __future__ import annotations

import base64
from typing import Any

_DEMO_EMAIL = "you@startup.com"


def _b64url(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii")


_TERMSHEET_HTML = (
    "<p>Hi,</p>"
    "<p>Great call today. Attaching the final redlines - one open point on the "
    "<b>liquidation preference</b> (we're proposing 1x non-participating).</p>"
    "<p>If that works, we can sign this week.</p>"
    "<p>Best,<br/>Dana</p>"
)


def _headers(d: dict[str, str]) -> list[dict[str, str]]:
    return [{"name": k, "value": v} for k, v in d.items()]


# Raw Gmail API ``threads().get(format="full")`` payloads, keyed by thread id.
# Mirrors the frontend dev-preview fixtures (mcp_server/dev_preview/src/fixtures.ts)
# so the reader renders the same content across preview and e2e.
_THREADS: dict[str, dict[str, Any]] = {
    "t-1001": {
        "id": "t-1001",
        "messages": [
            {
                "id": "m-1",
                "threadId": "t-1001",
                "snippet": "Great call today. Attaching the final redlines...",
                "internalDate": "1751706840000",
                "labelIds": ["INBOX", "UNREAD"],
                "payload": {
                    "mimeType": "multipart/mixed",
                    "headers": _headers(
                        {
                            "From": "Dana Whitfield <dana@northwind.vc>",
                            "To": _DEMO_EMAIL,
                            "Subject": "Series A term sheet - final redlines",
                            "Date": "Sat, 05 Jul 2026 09:14:00 +0000",
                        }
                    ),
                    "parts": [
                        {
                            "mimeType": "text/html",
                            "body": {
                                "data": _b64url(_TERMSHEET_HTML),
                                "size": len(_TERMSHEET_HTML),
                            },
                        },
                        {
                            "mimeType": "application/pdf",
                            "filename": "termsheet-v7.pdf",
                            "body": {"attachmentId": "att-1", "size": 184320},
                        },
                    ],
                },
            }
        ],
    },
}


class _Executable:
    """Stand-in for a googleapiclient request object: ``.execute()`` returns data."""

    def __init__(self, value: Any) -> None:
        self._value = value

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        return self._value


class _Threads:
    def get(self, userId: str, id: str, format: str | None = None) -> _Executable:  # noqa: A002 - `id`/`format` mirror googleapiclient's kwargs
        return _Executable(_THREADS.get(id, {"id": id, "messages": []}))


class _Drafts:
    def list(self, userId: str, maxResults: int | None = None) -> _Executable:
        return _Executable({"drafts": []})

    def get(self, userId: str, id: str, format: str | None = None) -> _Executable:  # noqa: A002 - `id`/`format` mirror googleapiclient's kwargs
        return _Executable({})


class _Attachments:
    def get(self, userId: str, messageId: str, id: str) -> _Executable:  # noqa: A002 - `id` mirrors googleapiclient's kwarg
        # No inline/cid images in the fixtures, so this is only hit defensively.
        return _Executable({"data": "", "size": 0})


class _Messages:
    def attachments(self) -> _Attachments:
        return _Attachments()


class _Users:
    def threads(self) -> _Threads:
        return _Threads()

    def drafts(self) -> _Drafts:
        return _Drafts()

    def messages(self) -> _Messages:
        return _Messages()

    def getProfile(self, userId: str) -> _Executable:
        return _Executable({"emailAddress": _DEMO_EMAIL})


class _FakeGmailResource:
    """Minimal fake of the googleapiclient Gmail v1 ``Resource``.

    Implements only the chained calls the Gmail services actually issue for the
    read/render paths. Anything else raises so an untested path can't silently
    pass in a faked run.
    """

    def users(self) -> _Users:
        return _Users()

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(
            f"fake Gmail backend does not implement '{name}'; add it to "
            "services/_gmail_fake_backend.py if a new e2e path needs it"
        )


def build_fake_gmail_client() -> _FakeGmailResource:
    """Return a fixture-serving fake Gmail client. E2E-only; see module docstring."""
    return _FakeGmailResource()
