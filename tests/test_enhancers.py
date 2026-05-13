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
        # Clean up after ourselves so the test stays idempotent under repeat runs.
        from mcp_server.enhancers import _enhancers

        name = "__test_dup_service"
        _enhancers.pop(name, None)
        try:

            @enhance(name)
            async def _first(tool):  # pragma: no cover
                return None

            with pytest.raises(ValueError, match="Duplicate enhancer"):

                @enhance(name)
                async def _second(tool):  # pragma: no cover
                    return None
        finally:
            _enhancers.pop(name, None)

    def test_get_enhancer_unknown_returns_none(self):
        assert get_enhancer("__definitely_not_a_real_service__") is None


class TestDoctorEnhancerFlows(TestTemplate):
    """Tests the doctor enhancer's elicitation flow at the unit level."""

    @staticmethod
    def _failing_result():
        from models.doctor import CheckResultModel, DoctorResult

        return DoctorResult(
            checks=[
                CheckResultModel(name="test", status="fail", message="x", fixable=True)
            ],
            has_failures=True,
        )

    @staticmethod
    def _passing_result():
        from models.doctor import DoctorResult

        return DoctorResult(checks=[], has_failures=False)

    def test_declined_elicit_does_not_set_fix(self):
        from mcp_server.enhancers.doctor import doctor_enhanced
        from models.doctor import DoctorInput, DoctorResult

        failing_result = self._failing_result()
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
        # Service called once (initial check) - no second call after decline.
        assert call_count == 1

    def test_accepted_elicit_re_runs_service_with_fix_true(self):
        from mcp_server.enhancers.doctor import doctor_enhanced
        from mcp_server.enhancers.schemas import ConfirmFix
        from models.doctor import DoctorInput, DoctorResult

        failing_result = self._failing_result()
        passing_result = self._passing_result()
        calls: list[DoctorInput] = []

        def fake_service(input_obj: DoctorInput) -> DoctorResult:
            calls.append(input_obj)
            return passing_result if input_obj.fix else failing_result

        ctx = _make_mock_ctx(
            elicit_result=AcceptedElicitation(
                action="accept", data=ConfirmFix(fix=True)
            )
        )
        tool: EnhancedTool[DoctorInput, DoctorResult] = EnhancedTool(
            ctx=ctx, input=DoctorInput(fix=False), service_fn=fake_service
        )
        result = asyncio.run(doctor_enhanced(tool))

        assert len(calls) == 2
        assert calls[0].fix is False
        assert calls[1].fix is True
        assert result.has_failures is False

    def test_accepted_elicit_with_fix_false_does_not_re_run(self):
        """User accepted the dialog but unchecked the box - should not re-run."""
        from mcp_server.enhancers.doctor import doctor_enhanced
        from mcp_server.enhancers.schemas import ConfirmFix
        from models.doctor import DoctorInput, DoctorResult

        failing_result = self._failing_result()
        call_count = 0

        def fake_service(_input: DoctorInput) -> DoctorResult:
            nonlocal call_count
            call_count += 1
            return failing_result

        ctx = _make_mock_ctx(
            elicit_result=AcceptedElicitation(
                action="accept", data=ConfirmFix(fix=False)
            )
        )
        tool: EnhancedTool[DoctorInput, DoctorResult] = EnhancedTool(
            ctx=ctx, input=DoctorInput(fix=False), service_fn=fake_service
        )
        result = asyncio.run(doctor_enhanced(tool))

        assert call_count == 1
        assert result.has_failures is True

    def test_does_not_elicit_when_input_already_has_fix_true(self):
        from mcp_server.enhancers.doctor import doctor_enhanced
        from models.doctor import DoctorInput, DoctorResult

        failing_result = self._failing_result()

        def fake_service(_input: DoctorInput) -> DoctorResult:
            return failing_result

        ctx = _make_mock_ctx()
        tool: EnhancedTool[DoctorInput, DoctorResult] = EnhancedTool(
            ctx=ctx, input=DoctorInput(fix=True), service_fn=fake_service
        )
        asyncio.run(doctor_enhanced(tool))

        ctx.elicit.assert_not_called()


class TestEnhancerCrashFallback(TestTemplate):
    """Verify @enhance(fallback=...) handling when the enhancer raises."""

    def _register_test_service_and_get_tool_fn(self, fallback_mode, enhancer_fn):
        """Register a throwaway service + enhancer, register the tool, return the wrapped fn."""
        from mcp.server.fastmcp import FastMCP

        from mcp_server._tool_factory import make_tool
        from mcp_server.enhancers import _enhancers, enhance
        from services import _registry, service

        class _CrashIn(BaseModel):
            x: int = 0

        class _CrashOut(BaseModel):
            value: int

        svc_name = f"__crash_test_{fallback_mode}"

        @service(
            name=svc_name,
            description="test",
            input_model=_CrashIn,
            output_model=_CrashOut,
        )
        def _svc(input: _CrashIn) -> _CrashOut:
            return _CrashOut(value=input.x * 100)

        enhance(svc_name, fallback=fallback_mode)(enhancer_fn)

        from services import get_registry

        entry = next(e for e in get_registry() if e.name == svc_name)
        test_mcp = FastMCP("test_crash")
        make_tool(test_mcp, entry)
        tool_fn = test_mcp._tool_manager._tools[svc_name].fn

        def cleanup():
            _registry[:] = [e for e in _registry if e.name != svc_name]
            _enhancers.pop(svc_name, None)

        return tool_fn, cleanup

    def test_crash_with_headless_fallback_returns_pure_service_result(self):
        async def crashing_enhancer(tool):
            raise RuntimeError("simulated enhancer failure")

        tool_fn, cleanup = self._register_test_service_and_get_tool_fn(
            "headless", crashing_enhancer
        )
        try:
            ctx = _make_mock_ctx()
            result = asyncio.run(tool_fn(ctx=ctx, x=7))
            # Result is a CallToolResult with the pure service's output
            assert result.structuredContent == {"value": 700}
        finally:
            cleanup()

    def test_crash_with_error_fallback_propagates(self):
        async def crashing_enhancer(tool):
            raise RuntimeError("boom")

        tool_fn, cleanup = self._register_test_service_and_get_tool_fn(
            "error", crashing_enhancer
        )
        try:
            ctx = _make_mock_ctx()
            with pytest.raises(RuntimeError, match="boom"):
                asyncio.run(tool_fn(ctx=ctx, x=1))
        finally:
            cleanup()

    def test_partial_output_discarded_on_crash(self):
        """If the enhancer attaches content/app meta and *then* crashes, the
        fallback CallToolResult must not ship that partial output."""

        async def crashing_after_partial(tool):
            tool.send_text("DO NOT SHIP THIS")
            tool.send_app("ui://should-be-discarded")
            raise RuntimeError("crash after partial")

        tool_fn, cleanup = self._register_test_service_and_get_tool_fn(
            "headless", crashing_after_partial
        )
        try:
            ctx = _make_mock_ctx()
            result = asyncio.run(tool_fn(ctx=ctx, x=3))
            assert result.structuredContent == {"value": 300}
            # Only the auto-generated text block; no DO NOT SHIP THIS.
            assert all(
                "DO NOT SHIP" not in c.text for c in result.content if c.type == "text"
            )
            assert result.meta is None
        finally:
            cleanup()


class TestBuildCallToolResultTypeGuard(TestTemplate):
    """_build_call_tool_result must reject non-BaseModel results loudly."""

    def test_rejects_non_basemodel_result(self):
        from mcp_server._tool_factory import _build_call_tool_result

        tool = EnhancedTool(ctx=_make_mock_ctx(), input=_Input(), service_fn=_service)
        with pytest.raises(TypeError, match="must be a Pydantic BaseModel"):
            _build_call_tool_result("not a model", tool)  # ty: ignore[invalid-argument-type]

    def test_accepts_basemodel_result(self):
        from mcp_server._tool_factory import _build_call_tool_result

        tool = EnhancedTool(ctx=_make_mock_ctx(), input=_Input(), service_fn=_service)
        result = _build_call_tool_result(_Output(doubled=4), tool)
        assert result.structuredContent == {"doubled": 4}


class TestConfigImageEnhancer(TestTemplate):
    """The config_show enhancer renders a PNG of the config tree."""

    def test_returns_base64_png_data(self):
        import base64

        from mcp_server.enhancers.config import _render_config_image

        data = _render_config_image({"a": 1, "b": {"c": "hello"}})
        assert data is not None
        decoded = base64.b64decode(data)
        # PNG magic bytes: 89 50 4E 47 0D 0A 1A 0A
        assert decoded[:8] == b"\x89PNG\r\n\x1a\n"

    def test_handles_empty_config(self):
        from mcp_server.enhancers.config import _render_config_image

        data = _render_config_image({})
        assert data is not None  # still produces a (small) image

    def test_handles_deeply_nested_config(self):
        """Depth limiter should kick in past 4 levels - should not infinite-recurse."""
        from mcp_server.enhancers.config import _format_config_lines

        nested: dict = {"a": 1}
        cur = nested
        for i in range(20):
            cur["nested"] = {"depth": i}
            cur = cur["nested"]

        lines = list(_format_config_lines(nested))
        assert any("..." in line for line in lines)

    def test_enhancer_attaches_image_when_capability_allows(self):
        from mcp_server.enhancers.config import config_show_enhanced
        from models.config import ConfigShowInput, ConfigShowResult

        def fake_service(_input: ConfigShowInput) -> ConfigShowResult:
            return ConfigShowResult(config={"hello": "world"})

        ctx = _make_mock_ctx()
        tool: EnhancedTool[ConfigShowInput, ConfigShowResult] = EnhancedTool(
            ctx=ctx, input=ConfigShowInput(), service_fn=fake_service
        )
        asyncio.run(config_show_enhanced(tool))

        assert len(tool.extra_content) == 1
        assert tool.extra_content[0].type == "image"
