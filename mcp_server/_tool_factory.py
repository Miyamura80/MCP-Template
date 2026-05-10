"""Internal tool-registration factory for `mcp_server.server`.

Builds FastMCP tools from `ServiceEntry` records, branching between two paths:

- **Headless**: sync wrapper, returns the Pydantic output model. FastMCP derives
  `outputSchema` from the return annotation.
- **Enhanced**: async wrapper with `Context`, calls an `@enhance`-registered
  function. Returns a `CallToolResult`. We patch `outputSchema` explicitly
  because FastMCP doesn't derive it when a tool returns `CallToolResult`.

Don't reach for these helpers from feature code - use `@service` and `@enhance`.
"""

import inspect
from typing import Any

from loguru import logger as log
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Context
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from mcp_server.enhancers import EnhancerEntry, get_enhancer
from mcp_server.enhancers.base import EnhancedTool
from services import ServiceEntry


def make_tool(mcp: FastMCP, entry: ServiceEntry) -> None:
    """Register a service as an MCP tool - enhanced if an enhancer exists, else headless."""
    enhancer_entry = get_enhancer(entry.name)
    if enhancer_entry is not None:
        _make_enhanced_tool(mcp, entry, enhancer_entry)
    else:
        _make_headless_tool(mcp, entry)


def _make_headless_tool(mcp: FastMCP, entry: ServiceEntry) -> None:
    """Sync wrapper. Returns the Pydantic output model so FastMCP derives outputSchema."""
    func = entry.func
    input_model = entry.input_model
    output_model = entry.output_model

    def tool_fn(**kwargs):
        input_obj = input_model(**kwargs)
        return func(input_obj)

    _apply_tool_signature(tool_fn, entry, return_annotation=output_model)
    mcp.tool(name=entry.name, description=entry.description)(tool_fn)


def _make_enhanced_tool(
    mcp: FastMCP, entry: ServiceEntry, enhancer_entry: EnhancerEntry
) -> None:
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
    _patch_output_schema(mcp, entry.name, output_model)


def _patch_output_schema(mcp: FastMCP, tool_name: str, output_model: type) -> None:
    """FastMCP doesn't derive outputSchema when a tool returns CallToolResult,
    so publish the schema explicitly from the service's output model."""
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
    serialized = (
        result.model_dump_json() if hasattr(result, "model_dump_json") else str(result)
    )
    content: list = [TextContent(type="text", text=serialized), *tool.extra_content]
    kwargs: dict = {"content": content, "structuredContent": structured}
    app_meta = tool.app_meta()
    if app_meta is not None:
        kwargs["_meta"] = app_meta
    return CallToolResult(**kwargs)
