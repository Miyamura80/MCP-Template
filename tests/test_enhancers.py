"""Tests for the MCP enhancer infrastructure (EnhancedTool, registration, fallback)."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.elicitation import AcceptedElicitation, DeclinedElicitation
from pydantic import BaseModel

from mcp_server.enhancers import enhance, get_enhancer
from mcp_server.enhancers.base import EnhancedTool
from tests.test_template import TestTemplate


class _Input(BaseModel):
    n: int = 0


class _Output(BaseModel):
    doubled: int


def _service(input: _Input) -> _Output:
    return _Output(doubled=input.n * 2)


def _make_mock_ctx(*, can_elicit: bool = True, elicit_result: Any = None) -> MagicMock:
    ctx = MagicMock()
    ctx.session.check_client_capability = MagicMock(return_value=can_elicit)
    ctx.elicit = AsyncMock(return_value=elicit_result)
    return ctx


class TestEnhancedTool(TestTemplate):
    def test_call_invokes_pure_service(self):
        ctx = _make_mock_ctx()
        tool = EnhancedTool(ctx=ctx, input=_Input(n=3), service_fn=_service)
        assert tool.call().doubled == 6

    def test_call_with_override_input(self):
        ctx = _make_mock_ctx()
        tool = EnhancedTool(ctx=ctx, input=_Input(n=3), service_fn=_service)
        result = tool.call(override_input=_Input(n=10))
        assert result.doubled == 20

    def test_can_elicit_true(self):
        tool = EnhancedTool(
            ctx=_make_mock_ctx(can_elicit=True),
            input=_Input(),
            service_fn=_service,
        )
        assert tool.can_elicit is True

    def test_can_elicit_false(self):
        tool = EnhancedTool(
            ctx=_make_mock_ctx(can_elicit=False),
            input=_Input(),
            service_fn=_service,
        )
        assert tool.can_elicit is False

    def test_can_show_app_default_true(self, monkeypatch):
        monkeypatch.delenv("MCP_DISABLE_APPS", raising=False)
        tool = EnhancedTool(ctx=_make_mock_ctx(), input=_Input(), service_fn=_service)
        assert tool.can_show_app is True

    def test_can_show_app_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("MCP_DISABLE_APPS", "1")
        tool = EnhancedTool(ctx=_make_mock_ctx(), input=_Input(), service_fn=_service)
        assert tool.can_show_app is False

    def test_send_text_appends_content(self):
        from mcp.types import TextContent

        tool = EnhancedTool(ctx=_make_mock_ctx(), input=_Input(), service_fn=_service)
        tool.send_text("hello")
        assert len(tool.extra_content) == 1
        block = tool.extra_content[0]
        assert isinstance(block, TextContent)
        assert block.text == "hello"

    def test_send_image_appends_content(self):
        tool = EnhancedTool(ctx=_make_mock_ctx(), input=_Input(), service_fn=_service)
        tool.send_image(data="abc", mime_type="image/png")
        assert len(tool.extra_content) == 1
        assert tool.extra_content[0].type == "image"

    def test_send_app_dual_keys_meta(self):
        tool = EnhancedTool(ctx=_make_mock_ctx(), input=_Input(), service_fn=_service)
        tool.send_app("ui://test/widget")
        meta = tool.app_meta()
        assert meta is not None
        assert meta["ui"]["resourceUri"] == "ui://test/widget"
        assert meta["ui/resourceUri"] == "ui://test/widget"

    def test_app_meta_none_when_no_app(self):
        tool = EnhancedTool(ctx=_make_mock_ctx(), input=_Input(), service_fn=_service)
        assert tool.app_meta() is None

    def test_elicit_passthrough(self):
        accepted = AcceptedElicitation(action="accept", data=_Input(n=42))
        ctx = _make_mock_ctx(elicit_result=accepted)
        tool = EnhancedTool(ctx=ctx, input=_Input(), service_fn=_service)
        result = asyncio.run(tool.elicit("confirm?", _Input))
        assert result is accepted


class TestEnhancerRegistry(TestTemplate):
    def test_get_enhancer_returns_registered(self):
        # Importing mcp_server.enhancers.doctor registers the doctor enhancer.
        import mcp_server.enhancers.doctor  # noqa: F401

        entry = get_enhancer("doctor")
        assert entry is not None
        assert entry.fallback == "headless"

    def test_duplicate_registration_raises(self):
        # Use a unique service name to avoid conflicting with real services.
        @enhance("__test_dup_service")
        async def _first(tool):  # pragma: no cover
            return None

        with pytest.raises(ValueError, match="Duplicate enhancer"):

            @enhance("__test_dup_service")
            async def _second(tool):  # pragma: no cover
                return None

    def test_get_enhancer_unknown_returns_none(self):
        assert get_enhancer("__definitely_not_a_real_service__") is None


class TestDeclinedElicitationDoesNotRetrigger(TestTemplate):
    """Tests the doctor enhancer's elicitation flow at the unit level."""

    def test_declined_elicit_does_not_set_fix(self):
        from mcp_server.enhancers.doctor import doctor_enhanced
        from models.doctor import CheckResultModel, DoctorInput, DoctorResult

        failing_result = DoctorResult(
            checks=[
                CheckResultModel(
                    name="test", status="fail", message="x", fixable=True
                )
            ],
            has_failures=True,
        )

        call_count = 0

        def fake_service(_input: DoctorInput) -> DoctorResult:
            nonlocal call_count
            call_count += 1
            return failing_result

        ctx = _make_mock_ctx(elicit_result=DeclinedElicitation(action="decline"))
        tool: EnhancedTool[DoctorInput, DoctorResult] = EnhancedTool(
            ctx=ctx, input=DoctorInput(fix=False), service_fn=fake_service
        )
        result = asyncio.run(doctor_enhanced(tool))

        assert result.has_failures is True
        # Service called once (initial check) — no second call after decline.
        assert call_count == 1
