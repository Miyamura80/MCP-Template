"""Enhancer registry - opt-in MCP-only wrappers for pure services.

Enhancers add MCP-specific behavior (elicitation, rich content, MCP Apps) to
services without polluting the pure `(Input) -> Output` service layer. They
are registered via `@enhance("service_name")` and looked up by
`mcp_server/server.py` at tool registration time. Services without an enhancer
take the headless path unchanged.

See `mcp_server/MCP_UI_ARCHITECTURE.md` and `mcp_server/MCP_UI_EDGE_CASES.md`.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from mcp_server.enhancers.base import EnhancedTool

FallbackMode = Literal["headless", "error"]

# Enhancer functions take an EnhancedTool wrapper around the pure service and
# return the service's output_model instance. Always async (we await them).
EnhancerFn = Callable[[EnhancedTool[Any, Any]], Awaitable[BaseModel]]


@dataclass(frozen=True)
class EnhancerEntry:
    fn: EnhancerFn
    fallback: FallbackMode


_enhancers: dict[str, EnhancerEntry] = {}


def enhance(service_name: str, fallback: FallbackMode = "headless"):
    """Register an MCP-specific enhancement for a pure service.

    fallback="headless" - if the enhancer raises, fall back to the pure service result.
    fallback="error" - propagate the exception (FastMCP turns it into isError).
    """

    def decorator(fn: EnhancerFn) -> EnhancerFn:
        if service_name in _enhancers:
            raise ValueError(f"Duplicate enhancer registration for {service_name!r}")
        _enhancers[service_name] = EnhancerEntry(fn=fn, fallback=fallback)
        return fn

    return decorator


def get_enhancer(service_name: str) -> EnhancerEntry | None:
    return _enhancers.get(service_name)
