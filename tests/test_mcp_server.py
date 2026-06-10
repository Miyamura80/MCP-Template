"""Tests for MCP server tool registration."""

import asyncio

from tests.test_template import TestTemplate


class TestMCPServer(TestTemplate):
    def test_server_imports(self):
        from mcp_server.server import mcp

        assert mcp is not None

    def test_all_services_registered_as_tools(self):
        # Importing server.py triggers service module imports + registration.
        # Some services are deliberately not exposed through the default MCP
        # surface, but they remain available to CLI/API transports.
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

    def test_default_mcp_surface_excludes_admin_and_demo_tools(self):
        from mcp_server.server import mcp

        tools = mcp._tool_manager._tools
        for tool_name in ("config_get", "config_set", "config_show", "doctor", "greet"):
            assert tool_name not in tools

    def test_enhanced_tools_publish_output_schema(self):
        from mcp_server.server import mcp

        for tool_name in ("gmail_compose", "gmail_curate_inbox", "gmail_get_thread"):
            tool = mcp._tool_manager._tools.get(tool_name)
            assert tool is not None, f"{tool_name} not registered"
            assert tool.output_schema is not None, f"{tool_name} missing outputSchema"

    def test_enhanced_tools_do_not_publish_context_as_input(self):
        from mcp_server.server import mcp

        for tool_name in ("gmail_compose", "gmail_curate_inbox", "gmail_get_thread"):
            tool = mcp._tool_manager._tools.get(tool_name)
            assert tool is not None, f"{tool_name} not registered"
            assert tool.context_kwarg == "ctx"
            assert "ctx" not in tool.parameters.get("properties", {})
            assert "ctx" not in tool.parameters.get("required", [])


class TestMCPServerIntegration(TestTemplate):
    """End-to-end integration tests calling tools through the registered FastMCP wrapper."""

    def test_app_resources_registered_and_serve_html(self):
        from mcp_server.server import mcp

        resources = asyncio.run(mcp.list_resources())
        uris = {str(r.uri) for r in resources}
        # gmail_composer / gmail_inbox apps are added in later phases; here we
        # only assert that whatever ui:// resources are registered serve HTML.
        ui_uris = [u for u in uris if u.startswith("ui://mymcp/")]
        assert ui_uris, "expected at least one ui://mymcp/ resource registered"
        for uri in ui_uris:
            contents = list(asyncio.run(mcp.read_resource(uri)))
            assert len(contents) == 1
            text = str(contents[0].content)
            assert text.lstrip().lower().startswith("<!doctype html>")

    def test_model_visible_focus_tool_returns_pydantic_model_directly(self):
        from mcp_server.server import mcp

        tool_fn = mcp._tool_manager._tools["gmail_get_focused_email"].fn
        result = tool_fn(user_id="test-user")
        assert result.focused is False
