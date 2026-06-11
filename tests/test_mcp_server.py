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

    def test_enhanced_output_schema_matches_output_model(self):
        # _patch_output_schema writes into FastMCP's private registry; assert
        # the published schema is byte-for-byte the service output model's
        # schema, not just non-None, so an SDK upgrade that breaks the patch
        # path fails loudly here.
        from pydantic import BaseModel

        from mcp_server.server import mcp
        from services import get_registry

        by_name = {e.name: e for e in get_registry()}
        for tool_name in ("gmail_compose", "gmail_curate_inbox", "gmail_get_thread"):
            tool = mcp._tool_manager._tools[tool_name]
            output_model = by_name[tool_name].output_model
            assert issubclass(output_model, BaseModel)
            expected = output_model.model_json_schema()
            assert tool.output_schema == expected, (
                f"{tool_name} outputSchema diverged from its output_model"
            )

    def test_app_rendering_tools_declare_ui_resource_in_tools_list(self):
        # Per the MCP Apps spec, tools that render an app must advertise the
        # ui:// resource in their tools/list _meta so hosts can pre-fetch the
        # HTML and apply CSP before the first call (found by MCPJam's
        # `apps conformance` check).
        import asyncio

        from mcp_server.server import mcp

        app_tools = {
            "gmail_compose",
            "gmail_update_draft",
            "gmail_reply_to_thread",
            "gmail_curate_inbox",
            "gmail_get_thread",
        }
        tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
        for name in app_tools:
            meta = tools[name].meta
            assert meta is not None, f"{name} missing _meta"
            uri = meta["ui"]["resourceUri"]
            assert uri.startswith("ui://mymcp/"), f"{name} bad resourceUri: {uri}"
            # Deprecated flat key kept for legacy host compat.
            assert meta["ui/resourceUri"] == uri

        # Headless tools must not grow UI metadata.
        assert tools["gmail_send"].meta is None
        # App-only companion tools keep visibility-only metadata.
        assert tools["gmail_inbox.refresh"].meta == {"ui": {"visibility": ["app"]}}

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

    def test_missing_app_build_serves_stub_comment(self):
        # Edge case A1 (mcp_server/MCP_UI_EDGE_CASES.md): an app dir without a
        # built dist/mcp-app.html must serve an HTML comment stub, not crash.
        from pathlib import Path

        from mcp.server.fastmcp import FastMCP

        from mcp_server.server import _register_app_resource

        test_mcp = FastMCP("test_stub")
        missing = Path("/nonexistent/test_app/dist/mcp-app.html")
        _register_app_resource(
            test_mcp, "ui://mymcp/test_stub_app", missing, "test_stub_app"
        )

        contents = list(asyncio.run(test_mcp.read_resource("ui://mymcp/test_stub_app")))
        assert len(contents) == 1
        text = str(contents[0].content)
        assert text.startswith("<!--")
        assert "test_stub_app not built" in text
        assert "make build_apps" in text

    def test_model_visible_focus_tool_returns_pydantic_model_directly(self):
        from mcp_server.server import mcp

        tool_fn = mcp._tool_manager._tools["gmail_get_focused_email"].fn
        result = tool_fn(user_id="test-user")
        assert result.focused is False
