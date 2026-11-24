"""``events/stream`` (push): the SSE frames a host receives on one request."""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from unittest.mock import patch

from common import global_config
from services.events import emit
from services.events.catalog import FEEDBACK_RECEIVED
from tests.test_mcp_e2e import _wire_client
from tests.test_mcp_protocol_eras import _MODERN
from tests.test_template import TestTemplate

_SUB = "io.modelcontextprotocol/subscriptionId"


@contextmanager
def _short_streams(max_seconds: float = 0.6, check_ms: int = 50):
    with (
        patch.object(global_config.events, "stream_max_seconds", max_seconds),
        patch.object(global_config.events, "stream_check_ms", check_ms),
    ):
        yield


def _stream(client, key, params: dict, request_id: int = 7) -> list[dict]:
    """POST events/stream (2026-07-28 wire) and return every SSE frame."""
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


def _feedback(user: str, i: int) -> None:
    emit(user, FEEDBACK_RECEIVED, {"id": f"f{i}", "category": "bug", "toolName": None})


class TestStream(TestTemplate):
    def test_active_then_events_then_final_result(self):
        with _wire_client("u-push") as (client, key), _short_streams():
            _feedback("u-push", 0)
            frames = _stream(client, key, {"name": FEEDBACK_RECEIVED, "cursor": "0"})
        methods = [f.get("method") for f in frames]
        assert methods[0] == "notifications/events/active"
        assert frames[0]["params"]["truncated"] is False
        events = [
            f["params"]
            for f in frames
            if f.get("method") == "notifications/events/event"
        ]
        assert [e["data"]["id"] for e in events] == ["f0"]
        assert events[0]["cursor"] == "1"
        assert all(f["params"]["_meta"][_SUB] == 7 for f in frames if "method" in f)
        assert frames[-1] == {"jsonrpc": "2.0", "id": 7, "result": frames[-1]["result"]}
        assert frames[-1]["result"]["resultType"] == "complete"

    def test_live_event_is_pushed(self):
        with _wire_client("u-live") as (client, key), _short_streams(max_seconds=1.0):

            def later():
                time.sleep(0.3)
                _feedback("u-live", 9)

            worker = threading.Thread(target=later)
            worker.start()
            frames = _stream(client, key, {"name": FEEDBACK_RECEIVED})  # from now
            worker.join()
        pushed = [
            f["params"]["data"]["id"]
            for f in frames
            if f.get("method") == "notifications/events/event"
        ]
        assert pushed == ["f9"]

    def test_heartbeat_when_quiet(self):
        with _wire_client("u-hb") as (client, key), _short_streams(max_seconds=1.3):
            frames = _stream(
                client, key, {"name": FEEDBACK_RECEIVED, "heartbeatMs": 1000}
            )
        beats = [
            f for f in frames if f.get("method") == "notifications/events/heartbeat"
        ]
        assert beats and "cursor" in beats[0]["params"]

    def test_only_the_principals_events(self):
        with _wire_client("u-mine") as (client, key), _short_streams():
            _feedback("someone-else", 1)
            frames = _stream(client, key, {"name": FEEDBACK_RECEIVED, "cursor": "0"})
        assert not [
            f for f in frames if f.get("method") == "notifications/events/event"
        ]

    def test_errors_are_jsonrpc_errors(self):
        with _wire_client("u-err") as (client, key), _short_streams():
            unknown = _stream(client, key, {"name": "nope"})
            future = _stream(client, key, {"name": FEEDBACK_RECEIVED, "cursor": "99"})
        assert unknown[-1]["error"]["code"] == -32023
        assert future[-1]["error"]["code"] == -32602

    def test_listed_as_push_capable(self):
        from services.events.catalog import EVENT_TYPES  # noqa: PLC0415

        assert all("push" in t.descriptor()["delivery"] for t in EVENT_TYPES.values())
