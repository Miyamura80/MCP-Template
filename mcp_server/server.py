"""FastMCP server that auto-registers all services from the registry as MCP tools."""

import inspect

from mcp.server.fastmcp import FastMCP

from services import ServiceEntry

mcp = FastMCP("mycli")


def _register_tools() -> None:
    """Import all service modules to populate the registry, then register as MCP tools."""
    import services.config_svc  # noqa: F401
    import services.doctor_svc  # noqa: F401
    import services.greet  # noqa: F401
    from services import get_registry

    for entry in get_registry():
        _make_tool(entry)


def _make_tool(entry: ServiceEntry) -> None:
    """Create an MCP tool from a service entry."""
    func = entry.func
    input_model = entry.input_model

    def tool_fn(**kwargs):
        try:
            input_obj = input_model(**kwargs)
            result = func(input_obj)
            return result.model_dump()
        except Exception as e:
            return {"error": str(e)}

    tool_fn.__name__ = entry.name
    tool_fn.__doc__ = entry.description
    sig = inspect.signature(input_model)
    tool_fn.__annotations__ = {k: v.annotation for k, v in sig.parameters.items()}
    tool_fn.__signature__ = sig  # type: ignore[attr-defined]
    mcp.tool(name=entry.name, description=entry.description)(tool_fn)


_register_tools()


def main() -> None:
    """Run the MCP server on stdio transport."""
    mcp.run(transport="stdio")
