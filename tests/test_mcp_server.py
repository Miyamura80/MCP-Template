"""Tests for MCP server tool registration."""

from tests.test_template import TestTemplate


class TestMCPServer(TestTemplate):
    def test_server_imports(self):
        from mcp_server.server import mcp

        assert mcp is not None

    def test_all_services_registered_as_tools(self):
        from services import get_registry

        registry = get_registry()
        service_names = {entry.name for entry in registry}

        assert "greet" in service_names
        assert "config_show" in service_names
        assert "doctor" in service_names

    def test_registry_entries_have_models(self):
        from services import get_registry

        for entry in get_registry():
            assert entry.input_model is not None
            assert entry.output_model is not None
            assert entry.func is not None
            assert entry.description
