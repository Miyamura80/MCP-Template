"""Tests for the streamable-HTTP /mcp endpoint mounted on the FastAPI app.

Covers the auth boundary (401 without creds, 200 with a valid API key) and a
smoke check that the MCP `initialize` handshake completes end-to-end.
"""

import json
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_server.auth.api_key_auth import create_api_key
from db import engine as db_engine
from db.base import Base
from tests.test_template import TestTemplate


def _patch_db():
    """Wire an in-memory SQLite into db.engine for the duration of the test."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    session_factory = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    db_engine._engine = eng
    db_engine._SessionLocal = session_factory
    return session_factory


def _read_sse_first_message(response) -> dict:
    """Parse the first ``data:`` line from an MCP SSE response."""
    for line in response.iter_lines():
        if isinstance(line, bytes):
            line = line.decode()
        if line.startswith("data:"):
            return json.loads(line.removeprefix("data:").strip())
    raise AssertionError("no SSE data frame in response")


class TestMCPRemote(TestTemplate):
    def test_mcp_requires_auth(self):
        from api_server.server import app

        # No `with`: the mcp_auth middleware short-circuits before the MCP sub-app
        # is reached, so we don't need to enter the lifespan / session manager.
        client = TestClient(app)
        resp = client.post(
                "/mcp",
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Host": "127.0.0.1:8080",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"},
                    },
                },
            )
        assert resp.status_code == 401
        body = resp.json()
        assert body["jsonrpc"] == "2.0"
        assert body["error"]["code"] == -32001

    @patch("api_server.middleware.mcp_auth.global_config")
    def test_mcp_initialize_with_api_key(self, mock_config):
        # No WorkOS -> middleware falls through to API-key path.
        mock_config.WORKOS_CLIENT_ID = None
        session_factory = _patch_db()
        with session_factory() as s:
            raw_key, _ = create_api_key(s, user_id="u-mcp-remote-test")

        from api_server.server import app

        with TestClient(app) as client:
            resp = client.post(
                "/mcp",
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Host": "127.0.0.1:8080",
                    "X-API-KEY": raw_key,
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"},
                    },
                },
            )
        assert resp.status_code == 200, resp.text
        msg = _read_sse_first_message(resp)
        assert msg["jsonrpc"] == "2.0"
        assert msg["result"]["serverInfo"]["name"] == "mymcp"

    def test_health_endpoint_unaffected_by_mcp_auth(self):
        from api_server.server import app

        # Plain TestClient (no lifespan) -- /health doesn't need the MCP session
        # manager and entering the lifespan would clobber other tests' use of it.
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
