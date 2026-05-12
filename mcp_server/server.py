"""FastMCP server that registers services as MCP tools and exposes app HTML resources.

Two registration paths (see `_tool_factory.py` for details):

- **Headless** (default): sync wrapper, returns the Pydantic output model directly.
- **Enhanced** (opt-in via `@enhance`): async wrapper with `Context`, may elicit
  user input, attach images/audio, or render an MCP App (iframe dashboard).

Primary transport is streamable HTTP, mounted on the FastAPI app at ``/mcp``
(see ``api_server/server.py``). Stdio is supported via the ``mymcp-mcp``
console script for local dev / Claude Desktop only.

See ``mcp_server/MCP_UI_ARCHITECTURE.md`` and ``mcp_server/MCP_UI_EDGE_CASES.md``.
"""

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from loguru import logger as log
from mcp.server.fastmcp import FastMCP

from mcp_server._tool_factory import make_tool

_APPS_DIR = Path(__file__).parent / "apps"
_APP_MIME_TYPE = "text/html;profile=mcp-app"

# Module-level singleton: app_tools / enhancers may import this at module-load
# time (e.g. ``@mcp.tool``), so it must exist before discovery runs.
mcp: FastMCP = FastMCP("mymcp")

_populated: bool = False


def build_mcp_server() -> FastMCP:
    """Populate the FastMCP singleton and return it. Idempotent."""
    global _populated
    if _populated:
        return mcp

    from mcp_server.app_tools import discover_app_tools
    from mcp_server.enhancers import discover_enhancers
    from services import discover_services, get_registry

    discover_services()
    discover_enhancers()
    discover_app_tools()

    for entry in get_registry():
        make_tool(mcp, entry)
    _register_app_resources(mcp)

    _populated = True
    return mcp


def _register_app_resources(mcp: FastMCP) -> None:
    """Register ui:// resources for each MCP App with a built dist/mcp-app.html."""
    if not _APPS_DIR.is_dir():
        return
    for app_dir in sorted(_APPS_DIR.iterdir()):
        if not app_dir.is_dir():
            continue
        html_path = app_dir / "dist" / "mcp-app.html"
        uri = f"ui://mymcp/{app_dir.name}"
        _register_app_resource(mcp, uri, html_path, app_dir.name)


def _register_app_resource(
    mcp: FastMCP, uri: str, html_path: Path, app_name: str
) -> None:
    @mcp.resource(uri, mime_type=_APP_MIME_TYPE, name=f"{app_name} app")
    def _read_app() -> str:
        if not html_path.exists():
            log.warning("MCP App {!r} missing build at {}", app_name, html_path)
            return f"<!-- {app_name} not built. Run `make build_apps`. -->"
        return html_path.read_text()


def mount_on(app, path: str = "/mcp") -> None:
    """Mount the streamable-HTTP MCP server onto a Starlette/FastAPI app.

    FastMCP's ``streamable_http_app()`` already serves at ``/mcp`` internally,
    so we mount it at root to avoid a doubled prefix. Caller must also include
    :func:`lifespan` in the parent app's lifespan to start the session manager.
    """
    if path != "/mcp":
        raise ValueError(
            "Custom mount paths are not supported; FastMCP serves at /mcp internally."
        )
    mcp = build_mcp_server()
    app.mount("/", mcp.streamable_http_app())


@asynccontextmanager
async def lifespan(_app):
    """Async context manager that runs FastMCP's streamable-HTTP session manager.

    The parent FastAPI app must include this in its ``lifespan=`` argument or
    incoming /mcp requests will fail with "Task group is not initialized".
    """
    mcp = build_mcp_server()
    sm = mcp.session_manager
    # StreamableHTTPSessionManager.run() refuses re-entry once _has_started is set.
    # Reset it so the same instance can be restarted (tests with --count, hot-reload).
    sm._has_started = False
    async with sm.run():
        yield


def main() -> None:
    """Run the MCP server on stdio transport (legacy / local-dev only)."""
    print(
        "[mymcp-mcp] stdio transport is legacy; "
        "prefer `mymcp-serve` and connect via streamable HTTP at /mcp.",
        file=sys.stderr,
    )
    server = build_mcp_server()
    server.run(transport="stdio")


# Populate the singleton at import time so tests / direct importers that reach
# into ``mcp._tool_manager`` see registered tools without an explicit build call.
build_mcp_server()
