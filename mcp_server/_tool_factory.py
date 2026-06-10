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


def _check_scopes() -> None:
    """Enforce ``services:execute`` scope for authenticated MCP tool calls.

    Skips silently when no authenticated user is bound (CLI / stdio).
    """
    from src.utils.current_user import current_user

    user = current_user()
    if user is None:
        return

    from api_server.auth.scopes import SERVICES_EXECUTE, check_scopes

    if not check_scopes([SERVICES_EXECUTE], user.scopes):
        raise PermissionError(
            "Insufficient permissions: 'services:execute' scope required."
        )


def _check_quota() -> None:
    """Enforce daily quota for authenticated MCP tool calls.

    Skips silently when no authenticated user is bound (CLI / stdio).
    Called *after* input validation so malformed requests don't burn quota.
    """
    from src.utils.current_user import current_user

    user = current_user()
    if user is None:
        return

    from api_server.billing.limits import ensure_daily_limit

    ensure_daily_limit(user.user_id)


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
        _check_scopes()
        if "user_id" in input_model.model_fields:  # ty: ignore[unresolved-attribute]
            from src.utils.current_user import current_user

            user = current_user()
            if user is not None:
                kwargs["user_id"] = user.user_id
            elif not kwargs.get("user_id"):
                kwargs.setdefault("user_id", "")
        input_obj = input_model(**kwargs)
        _check_quota()
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
        _check_scopes()
        if "user_id" in input_model.model_fields:  # ty: ignore[unresolved-attribute]
            from src.utils.current_user import current_user

            user = current_user()
            if user is not None:
                kwargs["user_id"] = user.user_id
            elif not kwargs.get("user_id"):
                kwargs.setdefault("user_id", "")
        input_obj = input_model(**kwargs)
        _check_quota()
        tool = EnhancedTool(ctx=ctx, input=input_obj, service_fn=func)
        try:
            result = await enhancer_entry.fn(tool)
        except Exception:  # noqa: BLE001
            # Enhancer failures of any kind must fall back to the pure service
            # so MCP clients still get a structured result on the headless path.
            if enhancer_entry.fallback == "error":
                raise
            log.exception(
                "enhancer for {!r} crashed; falling back to headless", entry.name
            )
            result = func(input_obj)
            # Discard any partial output the enhancer accumulated before crashing.
            tool.extra_content = []
            tool.app_resource_uri = None

        return _build_call_tool_result(result, tool)

    _apply_tool_signature(
        tool_fn, entry, return_annotation=CallToolResult, include_context=True
    )
    mcp.tool(name=entry.name, description=entry.description)(tool_fn)
    _patch_output_schema(mcp, entry.name, output_model)


def _patch_output_schema(mcp: FastMCP, tool_name: str, output_model: type) -> None:
    """Publish outputSchema for tools that return CallToolResult.

    FastMCP only derives outputSchema from a tool's return type annotation,
    and our enhanced wrappers return CallToolResult so the structured output
    is opaque to it. We patch the registered Tool's output_schema directly.

    KNOWN FRAGILITY: this reaches into `mcp._tool_manager._tools` (double-
    private). FastMCP has no public API for setting outputSchema on a tool
    that returns CallToolResult. Backstopped by integration tests in
    `tests/test_mcp_server.py::test_enhanced_tools_publish_output_schema`.
    """
    if not issubclass(output_model, BaseModel):
        return
    try:
        tools_registry = mcp._tool_manager._tools
    except AttributeError:
        log.warning(
            "FastMCP private _tool_manager._tools not found; outputSchema for "
            "{!r} will not be published. Likely an SDK upgrade incompatibility.",
            tool_name,
        )
        return
    tool = tools_registry.get(tool_name)
    if tool is None:
        log.warning(
            "Tool {!r} not found in FastMCP registry; outputSchema not published.",
            tool_name,
        )
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
    if include_context:
        annotations["ctx"] = Context
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


def _build_call_tool_result(result: BaseModel, tool: EnhancedTool) -> CallToolResult:
    """Assemble CallToolResult from a service result + accumulated extras.

    `result` must be a Pydantic BaseModel - the service's output_model
    instance. Other return types are rejected so outputSchema/structuredContent
    stay consistent and failures surface here rather than as opaque
    pydantic_core ValidationErrors deep inside CallToolResult.
    """
    if not isinstance(result, BaseModel):
        raise TypeError(
            f"Enhanced tool result must be a Pydantic BaseModel, got "
            f"{type(result).__name__}. Enhancers must return their service's "
            "output_model instance."
        )
    content: list = [
        TextContent(type="text", text=result.model_dump_json()),
        *tool.extra_content,
    ]
    kwargs: dict = {"content": content, "structuredContent": result.model_dump()}
    app_meta = tool.app_meta()
    if app_meta is not None:
        kwargs["_meta"] = app_meta
    return CallToolResult(**kwargs)
