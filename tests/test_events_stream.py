"""``events/stream`` (push): the SSE frames a host receives on one request."""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import anyio
import pytest
from mcp.shared.exceptions import MCPError

from common import global_config
from db.engine import use_db_session
from db.models.api_keys import APIKey
from db.models.events import EventLog
from mcp_server.events_stream import heartbeat_ms, stream_slot
from services.events import emit
from services.events.catalog import APPROVAL_RESOLVED, FEEDBACK_RECEIVED
from tests.test_mcp_e2e import _wire_client
from tests.test_mcp_protocol_eras import _MODERN
from tests.test_template import TestTemplate

_SUB = "io.modelcontextprotocol/subscriptionId"
_EVENT = "notifications/events/event"


@contextmanager
def _short_streams(max_seconds: float = 0.6, check_ms: int = 20, beat_ms: int = 30000):
    cfg = global_config.events
    with (
        patch.object(cfg, "stream_max_seconds", max_seconds),
        patch.object(cfg, "stream_check_ms", check_ms),
        patch.object(cfg, "stream_heartbeat_ms", beat_ms),
        patch.object(cfg, "stream_heartbeat_min_ms", 50),
    ):
        yield


def _stream(client, key, params: dict, request_id: int = 7) -> list[dict]:
    """POST events/stream (2026-07-28 wire) and return every frame."""
    params = {
        **params,
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": _MODERN,
            "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "0"},
            "io.modelcontextprotocol/clientCapabilities": {},
        },
    }
    resp = client.post(
        "/mcp",
        headers={
            "Accept": "application/json, text/event-stream",
            "Host": "127.0.0.1:8080",
            "X-API-KEY": key,
            "MCP-Protocol-Version": _MODERN,
            "Mcp-Method": "events/stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "events/stream",
            "params": params,
        },
    )
    if resp.headers.get("content-type", "").startswith("application/json"):
        return [resp.json()]  # an error before the stream opened: plain JSON-RPC
    assert resp.status_code == 200, resp.text
    return [
        json.loads(line.removeprefix("data:").strip())
        for line in resp.text.replace("\r\n", "\n").split("\n")
        if line.startswith("data:")
    ]


def _frames(frames: list[dict], method: str) -> list[dict]:
    return [f["params"] for f in frames if f.get("method") == method]


def _feedback(user: str, i: int) -> None:
    emit(user, FEEDBACK_RECEIVED, {"id": f"f{i}", "category": "bug", "toolName": None})


def _revoke_all_keys(timeout: float = 2.0) -> None:
    """Revoke every key, retrying until the revocation is really committed.

    The e2e client runs on one shared in-memory SQLite connection
    (``StaticPool``), so this thread's UPDATE shares a transaction with the
    stream's own auth check. When that check's session closes, its rollback can
    undo the UPDATE before this thread commits it. Production pools hand each
    session its own connection, so this only affects the test harness.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with use_db_session() as s:
            s.query(APIKey).update({"revoked": True})
            s.commit()
        with use_db_session() as s:
            if s.query(APIKey).filter_by(revoked=False).count() == 0:
                return
        time.sleep(0.01)
    raise AssertionError("could not revoke the test keys")


class TestStream(TestTemplate):
    def test_active_then_events_then_final_result(self):
        with _wire_client("u-push") as (client, key), _short_streams():
            _feedback("u-push", 0)
            frames = _stream(client, key, {"name": FEEDBACK_RECEIVED, "cursor": "0"})
        assert frames[0]["method"] == "notifications/events/active"
        assert frames[0]["params"]["truncated"] is False
        events = _frames(frames, _EVENT)
        assert [e["data"]["id"] for e in events] == ["f0"]
        assert events[0]["cursor"] == "1"
        assert all(f["params"]["_meta"][_SUB] == 7 for f in frames if "method" in f)
        final = frames[-1]
        assert final["id"] == 7 and final["result"]["resultType"] == "complete"
        assert final["result"]["_meta"][_SUB] == 7  # the spec's StreamEventsResult

    def test_live_event_is_pushed(self):
        with _wire_client("u-live") as (client, key), _short_streams(max_seconds=2.0):
            # Make the user's cursor row up front: the test DB is one shared
            # SQLite connection, and a first emit's savepoint would collide
            # with the stream's read on the other thread.
            emit("u-live", APPROVAL_RESOLVED, {"approvalId": "a", "tool": "t"})

            def later():
                time.sleep(0.3)
                _feedback("u-live", 9)

            worker = threading.Thread(target=later)
            worker.start()
            # From the cursor read before the emit, not "now": on a loaded
            # runner the emit can land before the stream reads its head, and
            # a null cursor would then (correctly) treat it as past.
            frames = _stream(client, key, {"name": FEEDBACK_RECEIVED, "cursor": "1"})
            worker.join()
        assert [e["data"]["id"] for e in _frames(frames, _EVENT)] == ["f9"]

    def test_heartbeat_when_quiet(self):
        with _wire_client("u-hb") as (client, key), _short_streams(max_seconds=0.6):
            frames = _stream(
                client, key, {"name": FEEDBACK_RECEIVED, "heartbeatMs": 100}
            )
        assert _frames(frames, "notifications/events/active")[0]["heartbeatMs"] == 100
        beats = _frames(frames, "notifications/events/heartbeat")
        assert len(beats) >= 2 and "cursor" in beats[0]

    def test_arguments_filter_and_cursor_moves_past_filtered(self):
        with _wire_client("u-args") as (client, key), _short_streams(max_seconds=0.5):
            for tool in ("gmail_send", "gmail_disconnect"):
                emit(
                    "u-args",
                    APPROVAL_RESOLVED,
                    {"approvalId": "a", "tool": tool, "status": "approved"},
                )
            frames = _stream(
                client,
                key,
                {
                    "name": APPROVAL_RESOLVED,
                    "cursor": "0",
                    "arguments": {"tool": "gmail_send"},
                    "heartbeatMs": 100,
                },
            )
        assert [e["data"]["tool"] for e in _frames(frames, _EVENT)] == ["gmail_send"]
        beats = _frames(frames, "notifications/events/heartbeat")
        assert beats[-1]["cursor"] == "2"  # past the filtered-out event

    def test_max_age_truncates_on_active(self):
        with _wire_client("u-age") as (client, key), _short_streams(max_seconds=0.2):
            _feedback("u-age", 1)
            with use_db_session() as s:
                s.query(EventLog).update(
                    {"created_at": datetime.now(UTC) - timedelta(hours=1)}
                )
                s.commit()
            frames = _stream(
                client,
                key,
                {"name": FEEDBACK_RECEIVED, "cursor": "0", "maxAgeMs": 1000},
            )
        assert _frames(frames, "notifications/events/active")[0]["truncated"] is True
        assert _frames(frames, _EVENT) == []

    def test_revoked_key_terminates_the_stream(self):
        with _wire_client("u-rev") as (client, key), _short_streams(max_seconds=2.0):

            def revoke():
                time.sleep(0.3)
                _revoke_all_keys()

            worker = threading.Thread(target=revoke)
            worker.start()
            started = time.monotonic()
            frames = _stream(
                client, key, {"name": FEEDBACK_RECEIVED, "heartbeatMs": 100}
            )
            worker.join()
        ended = _frames(frames, "notifications/events/terminated")
        assert ended and ended[0]["error"]["code"] == -32024
        assert time.monotonic() - started < 1.5  # ended early, not at the window
        assert frames[-1]["result"]["resultType"] == "complete"

    def test_only_the_principals_events(self):
        with _wire_client("u-mine") as (client, key), _short_streams():
            _feedback("someone-else", 1)
            frames = _stream(client, key, {"name": FEEDBACK_RECEIVED, "cursor": "0"})
        assert _frames(frames, _EVENT) == []

    def test_errors_are_jsonrpc_errors(self):
        with _wire_client("u-err") as (client, key), _short_streams():
            unknown = _stream(client, key, {"name": "nope"})
            future = _stream(client, key, {"name": FEEDBACK_RECEIVED, "cursor": "99"})
        assert unknown[-1]["error"]["code"] == -32023
        assert future[-1]["error"]["code"] == -32602

    def test_listed_as_push_capable(self):
        from services.events.catalog import EVENT_TYPES  # noqa: PLC0415

        assert all("push" in t.descriptor()["delivery"] for t in EVENT_TYPES.values())


class TestLimits(TestTemplate):
    def test_heartbeat_is_clamped_not_refused(self):
        cfg = global_config.events
        assert heartbeat_ms(1) == cfg.stream_heartbeat_min_ms
        assert heartbeat_ms(10**9) == cfg.stream_heartbeat_max_ms
        assert heartbeat_ms(None) == cfg.stream_heartbeat_ms

    def test_per_user_stream_cap(self):
        async def run() -> None:
            async with stream_slot("u"), stream_slot("u"):
                with pytest.raises(MCPError) as info:
                    async with stream_slot("u"):
                        pass
                assert info.value.error.code == -32025
                assert info.value.error.data == {"limit": "streams", "max": 2}
                async with stream_slot("someone-else"):
                    pass  # the cap is per user
            async with stream_slot("u"):
                pass  # slots are released when streams end

        with patch.object(global_config.events, "max_streams_per_user", 2):
            anyio.run(run)
