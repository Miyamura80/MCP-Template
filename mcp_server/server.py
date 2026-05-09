"""FastMCP server that auto-registers all services from the registry as MCP tools.

Two registration paths:

- **Headless** (default): sync wrapper, returns the Pydantic output model directly.
  FastMCP derives `outputSchema` from the return annotation. Exceptions propagate.
- **Enhanced** (opt-in via `@enhance`): async wrapper with `Context`. Calls the
  enhancer function, which may elicit user input, attach images/audio, or set
  app metadata. Returns a `CallToolResult` carrying both structured output and
  any extra content.

See `memory/project_mcp_ui_architecture.md` and `docs/EDGE_CASES.md`.
"""

import inspect
from pathlib import Path
from typing import Any

from loguru import logger as log
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Context
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from mcp_server.enhancers import EnhancerEntry, get_enhancer
from mcp_server.enhancers.base import EnhancedTool
from services import ServiceEntry

mcp = FastMCP("mycli")

_APPS_DIR = Path(__file__).parent / "apps"
_APP_MIME_TYPE = "text/html;profile=mcp-app"


def _register_tools() -> None:
    """Import service & enhancer modules to populate registries, then register MCP tools."""
    import mcp_server.app_tools.doctor_dashboard  # noqa: F401
    import mcp_server.enhancers.config  # noqa: F401
    import mcp_server.enhancers.doctor  # noqa: F401
    import services.config_svc  # noqa: F401
    import services.doctor_svc  # noqa: F401
    import services.greet  # noqa: F401
    from services import get_registry

    for entry in get_registry():
        _make_tool(entry)


def _register_app_resources() -> None:
    """Register ui:// resources for each MCP App with a built dist/mcp-app.html."""
    if not _APPS_DIR.is_dir():
        return
    for app_dir in sorted(_APPS_DIR.iterdir()):
        if not app_dir.is_dir():
            continue
        html_path = app_dir / "dist" / "mcp-app.html"
        uri = f"ui://mycli/{app_dir.name}"
        _register_app_resource(uri, html_path, app_dir.name)


def _register_app_resource(uri: str, html_path: Path, app_name: str) -> None:
    @mcp.resource(uri, mime_type=_APP_MIME_TYPE, name=f"{app_name} app")
    def _read_app() -> str:
        if not html_path.exists():
            log.warning("MCP App {!r} missing build at {}", app_name, html_path)
            return f"<!-- {app_name} not built. Run `make build_apps`. -->"
        return html_path.read_text()


def _make_tool(entry: ServiceEntry) -> None:
    enhancer_entry = get_enhancer(entry.name)
    if enhancer_entry is not None:
        _make_enhanced_tool(entry, enhancer_entry)
    else:
        _make_headless_tool(entry)


def _make_headless_tool(entry: ServiceEntry) -> None:
    """Sync wrapper. Returns the Pydantic output model so FastMCP can derive outputSchema."""
    func = entry.func
    input_model = entry.input_model
    output_model = entry.output_model

    def tool_fn(**kwargs):
        input_obj = input_model(**kwargs)
        return func(input_obj)

    _apply_tool_signature(tool_fn, entry, return_annotation=output_model)
    mcp.tool(name=entry.name, description=entry.description)(tool_fn)


def _make_enhanced_tool(entry: ServiceEntry, enhancer_entry: EnhancerEntry) -> None:
    """Async wrapper that calls the enhancer with an `EnhancedTool`."""
    func = entry.func
    input_model = entry.input_model
    output_model = entry.output_model

    async def tool_fn(ctx: Context, **kwargs) -> CallToolResult:
        input_obj = input_model(**kwargs)
        tool = EnhancedTool(ctx=ctx, input=input_obj, service_fn=func)
        try:
            result = await enhancer_entry.fn(tool)
        except Exception:
            if enhancer_entry.fallback == "error":
                raise
            log.exception(
                "enhancer for {!r} crashed; falling back to headless", entry.name
            )
            result = func(input_obj)
            tool = EnhancedTool(ctx=ctx, input=input_obj, service_fn=func)

        return _build_call_tool_result(result, tool)

    _apply_tool_signature(
        tool_fn, entry, return_annotation=CallToolResult, include_context=True
    )
    mcp.tool(name=entry.name, description=entry.description)(tool_fn)
    _patch_output_schema(entry.name, output_model)


def _patch_output_schema(tool_name: str, output_model: type) -> None:
    """FastMCP doesn't derive outputSchema when a tool returns CallToolResult, so
    publish the schema explicitly from the service's output model."""
    tool = mcp._tool_manager._tools.get(tool_name)
    if tool is None or not issubclass(output_model, BaseModel):
        return
    tool.__dict__["output_schema"] = output_model.model_json_schema()


def _apply_tool_signature(
    tool_fn: Any,
    entry: ServiceEntry,
    return_annotation: type,
    include_context: bool = False,
) -> None:
    """Synthesize __signature__ and __annotations__ so FastMCP derives input/output schema."""
    tool_fn.__name__ = entry.name
    tool_fn.__doc__ = entry.description

    input_sig = inspect.signature(entry.input_model)
    annotations: dict = {k: v.annotation for k, v in input_sig.parameters.items()}
    annotations["return"] = return_annotation
    tool_fn.__annotations__ = annotations

    params = list(input_sig.parameters.values())
    if include_context:
        ctx_param = inspect.Parameter(
            "ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Context
        )
        params = [ctx_param, *params]
    tool_fn.__signature__ = input_sig.replace(
        parameters=params, return_annotation=return_annotation
    )


def _build_call_tool_result(result, tool: EnhancedTool) -> CallToolResult:
    structured = result.model_dump() if hasattr(result, "model_dump") else result
    serialized = result.model_dump_json() if hasattr(result, "model_dump_json") else str(result)
    content: list = [TextContent(type="text", text=serialized), *tool.extra_content]
    kwargs: dict = {"content": content, "structuredContent": structured}
    app_meta = tool.app_meta()
    if app_meta is not None:
        kwargs["_meta"] = app_meta
    return CallToolResult(**kwargs)


_register_tools()
_register_app_resources()


def main() -> None:
    """Run the MCP server on stdio transport."""
    mcp.run(transport="stdio")
