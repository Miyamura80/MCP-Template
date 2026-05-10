"""Tests for MCP server tool registration."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from tests.test_template import TestTemplate


class TestMCPServer(TestTemplate):
    def test_server_imports(self):
        from mcp_server.server import mcp

        assert mcp is not None

    def test_all_services_registered_as_tools(self):
        # Importing server.py triggers service module imports + registration.
        import mcp_server.server  # noqa: F401
        from services import get_registry

        registry = get_registry()
        service_names = {entry.name for entry in registry}

        assert "greet" in service_names
        assert "config_show" in service_names
        assert "doctor" in service_names

    def test_registry_entries_have_models(self):
        import mcp_server.server  # noqa: F401
        from services import get_registry

        for entry in get_registry():
            assert entry.input_model is not None
            assert entry.output_model is not None
            assert entry.func is not None
            assert entry.description

    def test_enhanced_tools_publish_output_schema(self):
        from mcp_server.server import mcp

        for tool_name in ("doctor", "config_show", "greet"):
            tool = mcp._tool_manager._tools.get(tool_name)
            assert tool is not None, f"{tool_name} not registered"
            assert tool.output_schema is not None, f"{tool_name} missing outputSchema"


def _mock_ctx():
    ctx = MagicMock()
    ctx.session.check_client_capability = MagicMock(return_value=True)
    ctx.elicit = AsyncMock()
    return ctx


class TestMCPServerIntegration(TestTemplate):
    """End-to-end integration tests calling tools through the registered FastMCP wrapper."""

    def test_app_resource_registered_and_serves_html(self):
        from mcp_server.server import mcp

        resources = asyncio.run(mcp.list_resources())
        uris = {str(r.uri) for r in resources}
        assert "ui://mycli/doctor_dashboard" in uris

        contents = list(asyncio.run(mcp.read_resource("ui://mycli/doctor_dashboard")))
        assert len(contents) == 1
        text = str(contents[0].content)
        assert text.lstrip().lower().startswith("<!doctype html>")

    def test_app_only_tool_has_visibility_meta(self):
        from mcp_server.server import mcp

        tools = asyncio.run(mcp.list_tools())
        dash = next(t for t in tools if t.name == "doctor_dashboard.refresh")
        meta = getattr(dash, "meta", None) or getattr(dash, "_meta", None)
        assert meta == {"ui": {"visibility": ["app"]}}

    def test_doctor_returns_call_tool_result_with_app_meta(self):
        from mcp_server.server import mcp

        tool_fn = mcp._tool_manager._tools["doctor"].fn
        result = asyncio.run(tool_fn(ctx=_mock_ctx(), fix=True))

        assert result.meta is not None
        assert result.meta["ui"]["resourceUri"] == "ui://mycli/doctor_dashboard"
        assert result.meta["ui/resourceUri"] == "ui://mycli/doctor_dashboard"
        assert "has_failures" in (result.structuredContent or {})

    def test_doctor_omits_app_meta_when_disabled_via_env(self, monkeypatch):
        from mcp_server.server import mcp

        monkeypatch.setenv("MCP_DISABLE_APPS", "1")
        tool_fn = mcp._tool_manager._tools["doctor"].fn
        result = asyncio.run(tool_fn(ctx=_mock_ctx(), fix=True))
        assert result.meta is None

    def test_config_show_attaches_image_content_block(self):
        from mcp_server.server import mcp

        tool_fn = mcp._tool_manager._tools["config_show"].fn
        result = asyncio.run(tool_fn(ctx=_mock_ctx()))

        block_types = [c.type for c in result.content]
        assert "image" in block_types
        image_block = next(c for c in result.content if c.type == "image")
        assert image_block.mimeType == "image/png"

    def test_headless_greet_returns_pydantic_model_directly(self):
        from mcp_server.server import mcp
        from models.greet import GreetResult

        tool_fn = mcp._tool_manager._tools["greet"].fn
        result = tool_fn(name="World", shout=False, times=1)
        assert isinstance(result, GreetResult)
        assert result.message == "Hello, World!"
