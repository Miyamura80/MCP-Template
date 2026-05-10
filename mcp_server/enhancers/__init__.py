"""Enhancer registry — opt-in MCP-only wrappers for pure services.

Enhancers add MCP-specific behavior (elicitation, rich content, MCP Apps) to
services without polluting the pure `(Input) -> Output` service layer. They
are registered via `@enhance("service_name")` and looked up by
`mcp_server/server.py` at tool registration time. Services without an enhancer
take the headless path unchanged.

See `mcp_server/UI_ARCHITECTURE.md` and `mcp_server/UI_EDGE_CASES.md`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

FallbackMode = Literal["headless", "error"]


@dataclass(frozen=True)
class EnhancerEntry:
    fn: Callable
    fallback: FallbackMode


_enhancers: dict[str, EnhancerEntry] = {}


def enhance(service_name: str, fallback: FallbackMode = "headless"):
    """Register an MCP-specific enhancement for a pure service.

    fallback="headless" — if the enhancer raises, fall back to the pure service result.
    fallback="error" — propagate the exception (FastMCP turns it into isError).
    """

    def decorator(fn: Callable) -> Callable:
        if service_name in _enhancers:
            raise ValueError(f"Duplicate enhancer registration for {service_name!r}")
        _enhancers[service_name] = EnhancerEntry(fn=fn, fallback=fallback)
        return fn

    return decorator


def get_enhancer(service_name: str) -> EnhancerEntry | None:
    return _enhancers.get(service_name)
