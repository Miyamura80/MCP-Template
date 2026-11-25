"""SDK 2.x behavior shims: error-text pass-through and the elicitation gate."""

import asyncio
import binascii
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from loguru import logger
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from pydantic import BaseModel, Field

from api_server.auth.unified_auth import AuthenticatedUser
from mcp_server._tool_errors import model_facing_message
from mcp_server._tool_factory import make_tool
from mcp_server.enhancers.base import client_can_elicit
from mcp_server.server import _TemplateMCPServer
from services import InvalidArgument, ModelFacingError, ServiceEntry
from services.gmail_svc import GmailNotConnectedError
from src.utils.current_user import reset_current_user, set_current_user
from tests.test_template import TestTemplate

# Stands in for a foreign crash (SQLAlchemy, HTTP client, ...): an exception
# class whose defining module is outside the first-party packages.
_ThirdPartyError = type(
    "OperationalError", (Exception,), {"__module__": "sqlalchemy.exc"}
)
# A third-party ValueError subclass, like requests.InvalidURL or urllib3's
# LocationParseError: a builtin base must not make its text model-facing.
_ThirdPartyValueError = type(
    "LocationParseError", (ValueError,), {"__module__": "urllib3.exceptions"}
)


class _Upstream(BaseModel):
    count: int


def _billing_http_exception() -> HTTPException:
    """An HTTPException raised from a first-party billing module's frame."""
    namespace: dict[str, Any] = {
        "__name__": "api_server.billing.limits",
        "HTTPException": HTTPException,
    }
    exec(  # noqa: S102 - compiles a raise whose frame belongs to a billing module
        "def raise_quota():\n"
        "    raise HTTPException(status_code=429, detail="
        "{'code': 'rate_limited', 'message': 'Daily quota reached'})\n",
        namespace,
    )
    try:
        namespace["raise_quota"]()
    except HTTPException as exc:
        return exc
    raise AssertionError("unreachable")


def _fake_session(*, can_send: bool, declared: bool) -> SimpleNamespace:
    return SimpleNamespace(
        can_send_request=can_send,
        check_client_capability=lambda _capability: declared,
    )


class TestModelFacingMessage(TestTemplate):
    def test_model_facing_errors_keep_their_text(self):
        assert model_facing_message(ModelFacingError("do X next")) == "do X next"
        assert model_facing_message(InvalidArgument("bad arg")) == "bad arg"
        gmail = model_facing_message(GmailNotConnectedError("u1"))
        assert gmail is not None and gmail.startswith("No Gmail account")

    def test_billing_http_exception_keeps_its_message(self):
        assert model_facing_message(_billing_http_exception()) == "Daily quota reached"

    def test_http_exception_from_elsewhere_stays_sanitized(self):
        try:
            raise HTTPException(status_code=500, detail="upstream said: dsn=secret")
        except HTTPException as exc:
            assert model_facing_message(exc) is None

    def test_third_party_crashes_stay_sanitized(self):
        assert model_facing_message(_ThirdPartyError("dsn=secret")) is None
        assert model_facing_message(RuntimeError("internal")) is None
        assert model_facing_message(None) is None

    def test_builtin_bases_do_not_make_text_model_facing(self):
        assert model_facing_message(ValueError("bad arg")) is None
        assert model_facing_message(_ThirdPartyValueError("http://10.0.0.5:x")) is None
        assert model_facing_message(binascii.Error("Incorrect padding")) is None
        assert (
            model_facing_message(PermissionError(13, "denied", "/srv/secrets/key"))
            is None
        )
        try:
            _Upstream.model_validate({"count": "token=secret"})
        except ValueError as exc:
            assert model_facing_message(exc) is None


class TestTemplateServerErrorRelabel(TestTemplate):
    def _server_with(self, exc: Exception) -> _TemplateMCPServer:
        server = _TemplateMCPServer("errors-test")

        @server.tool(name="boom")
        def boom() -> str:
            raise exc

        return server

    def test_model_facing_failure_reaches_model(self):
        server = self._server_with(InvalidArgument("thread has no messages"))
        with pytest.raises(ToolError) as excinfo:
            asyncio.run(server.call_tool("boom", {}))
        assert not isinstance(excinfo.value, UnexpectedToolError)
        assert "thread has no messages" in str(excinfo.value)

    @pytest.mark.parametrize(
        "exc",
        [
            _ThirdPartyError("dsn=secret"),
            _ThirdPartyValueError("secret"),
            PermissionError(13, "Permission denied", "/srv/secret/key.pem"),
        ],
        ids=["third-party", "third-party-valueerror", "permissionerror-path"],
    )
    def test_third_party_failure_is_sanitized(self, exc):
        server = self._server_with(exc)
        with pytest.raises(UnexpectedToolError) as excinfo:
            asyncio.run(server.call_tool("boom", {}))
        assert "secret" not in str(excinfo.value)

    def test_upstream_validation_failure_is_sanitized(self):
        server = _TemplateMCPServer("errors-test")

        @server.tool(name="boom")
        def boom() -> str:
            _Upstream.model_validate({"count": "token=secret"})
            return "unreachable"

        with pytest.raises(UnexpectedToolError) as excinfo:
            asyncio.run(server.call_tool("boom", {}))
        assert "secret" not in str(excinfo.value)

    def test_crash_is_logged_with_its_traceback(self):
        server = self._server_with(_ThirdPartyError("dsn=secret"))
        records: list = []
        sink = logger.add(records.append, level="ERROR", format="{message}")
        try:
            with pytest.raises(UnexpectedToolError):
                asyncio.run(server.call_tool("boom", {}))
        finally:
            logger.remove(sink)
        assert len(records) == 1
        record = records[0].record
        assert record["level"].name == "ERROR"
        assert record["exception"] is not None
        assert record["exception"].type is _ThirdPartyError


class _In(BaseModel):
    a: int = Field(default=0, le=10)
    b: int = 0


class _Out(BaseModel):
    total: int


class TestToolWrapperRefusals(TestTemplate):
    """The wrappers' own refusals reach the model as ToolErrors."""

    def _server(self) -> _TemplateMCPServer:
        server = _TemplateMCPServer("wrapper-test")
        entry = ServiceEntry(
            name="adder",
            description="test",
            input_model=_In,
            output_model=_Out,
            func=lambda inp: _Out(total=inp.a + inp.b),
        )
        make_tool(server, entry)
        return server

    def test_scope_refusal_reaches_model(self):
        user = AuthenticatedUser(
            user_id="u-ro", auth_method="api_key", scopes=["services:read"]
        )
        token = set_current_user(user)
        try:
            with pytest.raises(ToolError) as excinfo:
                asyncio.run(self._server().call_tool("adder", {"a": 1, "b": 2}))
        finally:
            reset_current_user(token)
        assert not isinstance(excinfo.value, UnexpectedToolError)
        assert "services:execute" in str(excinfo.value)

    def test_input_model_rejection_reaches_model(self):
        with pytest.raises(ToolError) as excinfo:
            asyncio.run(self._server().call_tool("adder", {"a": 11, "b": 1}))
        assert not isinstance(excinfo.value, UnexpectedToolError)
        assert "less than or equal to 10" in str(excinfo.value)

    def test_valid_call_runs(self):
        result = self._server()._tool_manager._tools["adder"].fn(a=1, b=2)
        assert result.total == 3


class TestClientCanElicit(TestTemplate):
    def test_requires_declared_capability_and_back_channel(self):
        assert client_can_elicit(_fake_session(can_send=True, declared=True))

    def test_no_back_channel_means_no_elicitation(self):
        # Every 2026-07-28 request and stateless legacy HTTP.
        assert not client_can_elicit(_fake_session(can_send=False, declared=True))

    def test_undeclared_capability_means_no_elicitation(self):
        assert not client_can_elicit(_fake_session(can_send=True, declared=False))

    def test_no_session(self):
        assert not client_can_elicit(None)
        assert not client_can_elicit(SimpleNamespace(can_send_request=False))
