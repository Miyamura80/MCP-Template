"""Wire-level E2E tests for the streamable-HTTP /mcp endpoint.

Runs a full MCP session through the FastAPI mount - initialize,
notifications/initialized, tools/list, tools/call, resources/list,
resources/read - and asserts what actually crosses the wire: serialized
outputSchema, ``_meta.ui`` shapes, CallToolResult assembly, and
structuredContent. This is the fast in-CI tier of the two-tier strategy
from issue #42; MCPJam conformance (``make mcp_conformance``) is the
spec-conformance tier.

Complements ``tests/test_mcp_remote.py``, which covers the auth boundary
and stops at ``initialize``.
"""

import json
from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_server.auth.api_key_auth import create_api_key
from api_server.server import app
from db import engine as db_engine
from db.base import Base
from mcp_server._tool_factory import make_tool
from mcp_server.enhancers import _enhancers, enhance
from mcp_server.server import mcp
from services import _registry, get_registry, service
from tests.test_template import TestTemplate

_PROTOCOL_VERSION = "2025-03-26"


@contextmanager
def _patch_db():
    """Wire an in-memory SQLite into db.engine for the duration of the block."""
    orig_engine = db_engine._engine
    orig_session = db_engine._SessionLocal
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    session_factory = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    db_engine._engine = eng
    db_engine._SessionLocal = session_factory
    try:
        yield session_factory
    finally:
        db_engine._engine = orig_engine
        db_engine._SessionLocal = orig_session


def _read_sse_first_message(response) -> dict:
    """Parse the first ``data:`` line from an MCP SSE response."""
    for line in response.iter_lines():
        if isinstance(line, bytes):
            line = line.decode()
        if line.startswith("data:"):
            return json.loads(line.removeprefix("data:").strip())
    raise AssertionError("no SSE data frame in response")


class _McpSession:
    """Minimal JSON-RPC client speaking streamable HTTP against a TestClient."""

    def __init__(self, client: TestClient, api_key: str):
        self._client = client
        self._api_key = api_key
        self._session_id: str | None = None
        self._next_id = 0
        self._protocol_version = _PROTOCOL_VERSION

    def _headers(self) -> dict:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Host": "127.0.0.1:8080",
            "X-API-KEY": self._api_key,
            # Post-initialize requests must carry the *negotiated* version,
            # not the one the client originally requested.
            "MCP-Protocol-Version": self._protocol_version,
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        return headers

    def request(self, method: str, params: dict | None = None) -> dict:
        """POST a JSON-RPC request; return the `result` member of the response."""
        self._next_id += 1
        resp = self._client.post(
            "/mcp",
            headers=self._headers(),
            json={
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": method,
                "params": params or {},
            },
        )
        assert resp.status_code == 200, f"{method}: {resp.status_code} {resp.text}"
        if self._session_id is None:
            self._session_id = resp.headers.get("mcp-session-id")
        msg = _read_sse_first_message(resp)
        assert "error" not in msg, f"{method} returned error: {msg.get('error')}"
        return msg["result"]

    def notify(self, method: str) -> None:
        resp = self._client.post(
            "/mcp",
            headers=self._headers(),
            json={"jsonrpc": "2.0", "method": method},
        )
        assert resp.status_code == 202, f"{method}: {resp.status_code} {resp.text}"

    def handshake(self) -> dict:
        result = self.request(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "e2e-test", "version": "0"},
            },
        )
        self._protocol_version = result["protocolVersion"]
        self.notify("notifications/initialized")
        return result


@contextmanager
def _wire_session(user_id: str):
    """Yield a handshaken MCP session backed by a fresh DB and API key."""
    with patch("api_server.middleware.mcp_auth.global_config") as mock_config:
        mock_config.WORKOS_CLIENT_ID = None
        mock_config.WORKOS_AUTHKIT_DOMAIN = None
        with _patch_db() as session_factory:
            with session_factory() as s:
                raw_key, _ = create_api_key(s, user_id=user_id, scopes=["*"])

            with TestClient(app) as client:
                session = _McpSession(client, raw_key)
                session.handshake()
                yield session


class TestMCPWireE2E(TestTemplate):
    def test_tools_list_serializes_schemas_and_app_meta(self):
        with _wire_session("u-e2e-list") as session:
            tools = {t["name"]: t for t in session.request("tools/list")["tools"]}

            # Enhanced tools publish outputSchema even though they return
            # CallToolResult (the _patch_output_schema path), and declare
            # their ui:// resource in tools/list per the MCP Apps spec.
            compose = tools["gmail_compose"]
            assert compose["outputSchema"]["type"] == "object"
            assert compose["_meta"]["ui"]["resourceUri"].startswith("ui://mymcp/")
            assert (
                compose["_meta"]["ui/resourceUri"]
                == (compose["_meta"]["ui"]["resourceUri"])
            )

            # Headless tools carry no UI metadata on the wire.
            assert "_meta" not in tools["gmail_send"] or (
                tools["gmail_send"].get("_meta") in (None, {})
            )

    def test_tools_call_returns_structured_content(self):
        with _wire_session("u-e2e-call") as session:
            result = session.request(
                "tools/call", {"name": "gmail_get_focused_email", "arguments": {}}
            )
            # No focused thread for a fresh user; auth guard substitutes the
            # verified principal for the (empty) wire user_id.
            assert result["structuredContent"]["focused"] is False
            assert result["isError"] is False
            text_block = result["content"][0]
            assert text_block["type"] == "text"
            assert json.loads(text_block["text"])["focused"] is False

    def test_resources_read_serves_app_html(self):
        with _wire_session("u-e2e-res") as session:
            resources = session.request("resources/list")["resources"]
            ui_resources = [
                r for r in resources if str(r["uri"]).startswith("ui://mymcp/")
            ]
            assert ui_resources, "expected ui:// resources in resources/list"
            assert all(
                r["mimeType"] == "text/html;profile=mcp-app" for r in ui_resources
            )

            contents = session.request(
                "resources/read", {"uri": ui_resources[0]["uri"]}
            )["contents"]
            assert len(contents) == 1
            assert contents[0]["mimeType"] == "text/html;profile=mcp-app"
            assert contents[0]["text"].lstrip().lower().startswith("<!doctype html>")

    def test_enhanced_tool_call_assembles_full_result_on_wire(self):
        """Register a throwaway enhanced tool and call it through the wire,
        asserting the CallToolResult parts (text + extra content, _meta.ui,
        structuredContent) as serialized - not via in-process imports."""

        class _In(BaseModel):
            x: int = 0

        class _Out(BaseModel):
            value: int

        svc_name = "__e2e_enhanced_test"

        @service(name=svc_name, description="e2e", input_model=_In, output_model=_Out)
        def _svc(input: _In) -> _Out:
            return _Out(value=input.x * 3)

        @enhance(svc_name, fallback="headless")
        async def _enhancer(tool):
            result = tool.call()
            tool.send_text("extra block", audience=["user"])
            tool.send_app("ui://mymcp/__e2e_test_app")
            return result

        entry = next(e for e in get_registry() if e.name == svc_name)
        make_tool(mcp, entry)
        try:
            with _wire_session("u-e2e-enh") as session:
                result = session.request(
                    "tools/call", {"name": svc_name, "arguments": {"x": 7}}
                )
                assert result["structuredContent"] == {"value": 21}
                blocks = result["content"]
                assert json.loads(blocks[0]["text"]) == {"value": 21}
                assert blocks[1]["text"] == "extra block"
                assert blocks[1]["annotations"]["audience"] == ["user"]
                meta = result["_meta"]
                assert meta["ui"]["resourceUri"] == "ui://mymcp/__e2e_test_app"
                assert meta["ui/resourceUri"] == "ui://mymcp/__e2e_test_app"
        finally:
            _registry[:] = [e for e in _registry if e.name != svc_name]
            _enhancers.pop(svc_name, None)
            mcp._tool_manager._tools.pop(svc_name, None)
