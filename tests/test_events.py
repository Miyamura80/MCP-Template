"""MCP Events (SEP-3415): emit, poll, webhook subscribe/verify/deliver, wire."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest

from common import global_config
from common.token_encryption import PlaintextEncryption
from db.models.events import EventLog, EventSubscription
from db.models.webhooks import WebhookDelivery
from mcp_server.events_ext import EXTENSION_ID
from services import InvalidArgument
from services.events import emit
from services.events.catalog import APPROVAL_RESOLVED, FEEDBACK_RECEIVED
from services.events.store import UnknownEventError, poll, prune, purge_user
from services.events.webhooks import (
    CallbackEndpointError,
    secret_key,
    subscribe,
    unsubscribe,
)
from services.webhook_delivery_svc import drain_due_deliveries
from tests.test_mcp_e2e import _patch_db, _wire_client
from tests.test_mcp_protocol_eras import _ModernMcpClient
from tests.test_template import TestTemplate
from tests.test_webhooks import _PUBLIC_ADDRINFO, _mock_http

_URL = "https://hooks.example.com/mcp"


def _secret() -> str:
    return "whsec_" + base64.b64encode(secrets.token_bytes(32)).decode()


@contextmanager
def _env():
    """In-memory DB, plaintext secrets, no real DNS."""
    with ExitStack() as stack:
        factory = stack.enter_context(_patch_db())
        stack.enter_context(
            patch(
                "services.events.webhooks.require_encryption",
                return_value=PlaintextEncryption(),
            )
        )
        stack.enter_context(
            patch(
                "services.webhooks_svc.socket.getaddrinfo",
                return_value=_PUBLIC_ADDRINFO,
            )
        )
        yield factory


class _Receiver:
    """A webhook endpoint: answers the challenge, records deliveries."""

    def __init__(self, secret: str, status: int = 200, echo: bool = True) -> None:
        self.secret = secret
        self.status = status
        self.echo = echo
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        if body.get("type") == "verification":
            if not self.echo:
                return httpx.Response(200, json={"challenge": "wrong"})
            return httpx.Response(200, json={"challenge": body["challenge"]})
        return httpx.Response(self.status)

    def verify(self, request: httpx.Request) -> bool:
        """What a Standard Webhooks receiver checks."""
        signed = (
            f"{request.headers['webhook-id']}.{request.headers['webhook-timestamp']}."
        ).encode() + request.content
        mac = hmac.new(secret_key(self.secret), signed, hashlib.sha256).digest()
        expected = "v1," + base64.b64encode(mac).decode()
        return hmac.compare_digest(expected, request.headers["webhook-signature"])

    def events(self) -> list[dict]:
        return [
            json.loads(r.content)
            for r in self.requests
            if "type" not in json.loads(r.content)
        ]


def _subscribe(user="u", name=FEEDBACK_RECEIVED, secret=None, **kw):
    return subscribe(
        user,
        name,
        kw.pop("arguments", {}),
        url=kw.pop("url", _URL),
        secret=secret or _secret(),
        cursor=kw.pop("cursor", None),
        max_age_ms=kw.pop("max_age_ms", None),
        ttl_ms=kw.pop("ttl_ms", None),
    )


def _poll(user="u", name=FEEDBACK_RECEIVED, cursor=None, **kw):
    return poll(
        user,
        name,
        kw.pop("arguments", {}),
        cursor=cursor,
        max_age_ms=kw.pop("max_age_ms", None),
        max_events=kw.pop("max_events", None),
    )


def _feedback(user="u", i=0) -> str:
    return emit(
        user, FEEDBACK_RECEIVED, {"id": f"f{i}", "category": "bug", "toolName": None}
    )


class TestPoll(TestTemplate):
    def test_null_cursor_starts_from_now(self):
        with _env():
            _feedback()
            first = _poll()
            assert first["events"] == [] and first["truncated"] is False
            _feedback(i=1)
            got = _poll(cursor=first["cursor"])
            assert [e["data"]["id"] for e in got["events"]] == ["f1"]
            assert set(got["events"][0]) == {"eventId", "name", "timestamp", "data"}
            assert _poll(cursor=got["cursor"])["events"] == []

    def test_only_the_principals_events(self):
        with _env():
            start = _poll()["cursor"]
            _feedback(user="other")
            assert _poll(cursor=start)["events"] == []

    def test_arguments_filter(self):
        with _env():
            start = _poll(name=APPROVAL_RESOLVED)["cursor"]
            for tool in ("gmail_send", "gmail_disconnect"):
                emit(
                    "u",
                    APPROVAL_RESOLVED,
                    {"approvalId": "a", "tool": tool, "status": "approved"},
                )
            got = _poll(
                name=APPROVAL_RESOLVED, cursor=start, arguments={"tool": "gmail_send"}
            )
            assert [e["data"]["tool"] for e in got["events"]] == ["gmail_send"]

    def test_has_more_and_paging(self):
        with _env():
            start = _poll()["cursor"]
            for i in range(3):
                _feedback(i=i)
            page = _poll(cursor=start, max_events=2)
            assert len(page["events"]) == 2 and page["hasMore"] is True
            assert page["nextPollMs"] == 0
            rest = _poll(cursor=page["cursor"], max_events=2)
            assert len(rest["events"]) == 1 and rest["hasMore"] is False

    def test_pruned_cursor_is_truncated(self):
        with _env() as factory:
            start = _poll()["cursor"]
            _feedback(i=0)
            _feedback(i=1)
            with factory() as s:  # age the first one out
                first = s.query(EventLog).order_by(EventLog.seq).first()
                s.query(EventLog).filter_by(seq=first.seq).update(
                    {"created_at": datetime.now(UTC) - timedelta(days=30)}
                )
                s.commit()
            assert prune() == 1
            got = _poll(cursor=start)
            assert got["truncated"] is True
            assert [e["data"]["id"] for e in got["events"]] == ["f1"]

    def test_bad_inputs(self):
        with _env():
            with pytest.raises(UnknownEventError):
                _poll(name="nope.happened")
            with pytest.raises(InvalidArgument):
                _poll(cursor="not-a-cursor")
            with pytest.raises(InvalidArgument):
                _poll(arguments={"tool": "x"})  # feedback.received takes none


class TestEmitters(TestTemplate):
    def test_feedback_emits(self):
        from models.feedback import FeedbackInput  # noqa: PLC0415
        from services.feedback_svc import feedback  # noqa: PLC0415

        with _env():
            start = _poll()["cursor"]
            result = feedback(FeedbackInput(user_id="u", message="hi", category="bug"))
            got = _poll(cursor=start)["events"]
            assert got[0]["data"] == {
                "id": result.id,
                "category": "bug",
                "toolName": None,
            }

    def test_approval_decision_emits(self):
        from services.approvals_svc import deny  # noqa: PLC0415
        from tests.test_approvals import _danger_tool, _held, _set_mode  # noqa: PLC0415

        with _env(), _danger_tool() as entry:
            start = _poll(name=APPROVAL_RESOLVED)["cursor"]
            _set_mode("u", "browser")
            view = _held(entry, "u")
            deny("u", view.id)
            got = _poll(name=APPROVAL_RESOLVED, cursor=start)["events"]
            assert got[0]["data"] == {
                "approvalId": view.id,
                "tool": entry.name,
                "status": "denied",
            }

    def test_gmail_purge(self):
        with _env():
            start = _poll(name="gmail.message_received")["cursor"]
            emit(
                "u", "gmail.message_received", {"message_id": "m", "subject": "secret"}
            )
            assert purge_user("u", "gmail.message_received") == 1
            assert _poll(name="gmail.message_received", cursor=start)["events"] == []


class TestWebhook(TestTemplate):
    def test_secret_format(self):
        for bad in ("abc", "whsec_!!!", "whsec_" + base64.b64encode(b"short").decode()):
            with pytest.raises(InvalidArgument):
                secret_key(bad)

    def test_https_required(self):
        with (
            _env(),
            patch.object(global_config, "DEV_ENV", "prod"),
            pytest.raises(InvalidArgument, match="https"),
        ):
            _subscribe(url="http://hooks.example.com/x")

    def test_verify_then_deliver_signed(self):
        secret = _secret()
        receiver = _Receiver(secret)
        with _env(), _mock_http(receiver):
            result = _subscribe(secret=secret)
            assert result["id"].startswith("sub_") and result["refreshBefore"]
            assert json.loads(receiver.requests[0].content)["type"] == "verification"
            event_id = _feedback()
            assert drain_due_deliveries()["sent"] == 1
        delivered = receiver.requests[-1]
        assert receiver.verify(delivered)
        assert delivered.headers["X-MCP-Subscription-Id"] == result["id"]
        body = receiver.events()[0]
        assert body["eventId"] == event_id and body["cursor"]

    def test_failed_challenge(self):
        secret = _secret()
        with _env(), _mock_http(_Receiver(secret, echo=False)):
            with pytest.raises(CallbackEndpointError) as info:
                _subscribe(secret=secret)
            assert info.value.reason == "challenge_failed"

    def test_refresh_is_idempotent_and_ttl_capped(self):
        secret = _secret()
        with _env() as factory, _mock_http(_Receiver(secret)):
            a = _subscribe(secret=secret, ttl_ms=10**12)
            b = _subscribe(secret=secret)
            assert a["id"] == b["id"]
            with factory() as s:
                assert s.query(EventSubscription).count() == 1
            refresh = datetime.fromisoformat(a["refreshBefore"])
            assert refresh - datetime.now(UTC) <= timedelta(days=7, seconds=5)

    def test_gone_is_not_retried(self):
        secret = _secret()
        with _env() as factory, _mock_http(_Receiver(secret, status=410)):
            _subscribe(secret=secret)
            _feedback()
            assert drain_due_deliveries()["failed"] == 1
            with factory() as s:
                row = s.query(WebhookDelivery).one()
                assert (row.status, row.attempts) == ("failed", 1)

    def test_server_errors_retry(self):
        secret = _secret()
        with _env() as factory, _mock_http(_Receiver(secret, status=503)):
            _subscribe(secret=secret)
            _feedback()
            assert drain_due_deliveries()["retry"] == 1
            with factory() as s:
                assert s.query(WebhookDelivery).one().status == "pending"

    def test_unsubscribe_and_expiry_stop_delivery(self):
        secret = _secret()
        receiver = _Receiver(secret)
        with _env() as factory, _mock_http(receiver):
            _subscribe(secret=secret)
            unsubscribe("u", FEEDBACK_RECEIVED, {}, _URL)
            _feedback()
            assert drain_due_deliveries()["sent"] == 0
            _subscribe(secret=secret)
            _feedback(i=1)
            with factory() as s:  # expires before the runner gets to it
                s.query(EventSubscription).update(
                    {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
                )
                s.commit()
            assert drain_due_deliveries()["dropped"] == 1
        assert receiver.events() == []

    def test_subscribe_with_cursor_replays(self):
        secret = _secret()
        receiver = _Receiver(secret)
        with _env(), _mock_http(receiver):
            start = _poll()["cursor"]
            _feedback(i=7)
            _subscribe(secret=secret, cursor=start)
            drain_due_deliveries()
        assert [e["data"]["id"] for e in receiver.events()] == ["f7"]


class TestWire(TestTemplate):
    def test_discover_and_list(self):
        with _wire_client("u-ev") as (client, key):
            modern = _ModernMcpClient(client, key)
            caps = modern.request("server/discover")["capabilities"]
            assert caps["extensions"][EXTENSION_ID] == {}
            types = {t["name"]: t for t in modern.request("events/list")["events"]}
        assert set(types) == {
            "approval.resolved",
            "feedback.received",
            "gmail.message_received",
        }
        assert types["approval.resolved"]["delivery"] == ["poll", "push", "webhook"]
        assert "tool" in types["approval.resolved"]["inputSchema"]["properties"]

    def test_poll_over_the_wire(self):
        with _wire_client("u-ev2") as (client, key):
            modern = _ModernMcpClient(client, key)
            start = modern.request("events/poll", {"name": FEEDBACK_RECEIVED})
            _feedback(user="u-ev2", i=3)
            got = modern.request(
                "events/poll", {"name": FEEDBACK_RECEIVED, "cursor": start["cursor"]}
            )
            assert got["resultType"] == "complete"
            assert [e["data"]["id"] for e in got["events"]] == ["f3"]

    def test_error_codes(self):
        with _wire_client("u-ev3") as (client, key):
            modern = _ModernMcpClient(client, key)
            missing = modern.request_error("events/poll", {"name": "nope"})
            assert missing["code"] == -32023 and missing["data"]["kind"] == "event"
            bad = modern.request_error(
                "events/poll", {"name": FEEDBACK_RECEIVED, "cursor": "zzz"}
            )
            assert bad["code"] == -32602
