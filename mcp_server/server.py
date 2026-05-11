"""FastMCP server that registers services as MCP tools and exposes app HTML resources.

**Remote-first.** By default this server runs on **Streamable HTTP** so it can
be hosted (Smithery, Cloudflare, Railway, your own infra) and reached by any
MCP-aware Host over the network. Stdio is still supported for local editor
integrations via the ``--stdio`` flag, but treat it as the fallback.

Two tool registration paths (see ``_tool_factory.py`` for details):

- **Headless** (default): sync wrapper, returns the Pydantic output model directly.
- **Enhanced** (opt-in via ``@enhance``): async wrapper with ``Context``, may elicit
  user input, attach images/audio, or render an MCP App (iframe dashboard).

See ``mcp_server/MCP_UI_ARCHITECTURE.md`` and ``mcp_server/MCP_UI_EDGE_CASES.md``.
"""

import argparse
import os
from pathlib import Path

from loguru import logger as log
from mcp.server.fastmcp import FastMCP

from common import global_config
from mcp_server._tool_factory import make_tool

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
        make_tool(mcp, entry)


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


_register_tools()
_register_app_resources()


def _parse_args() -> argparse.Namespace:
    cfg = global_config.mcp_server
    parser = argparse.ArgumentParser(
        prog="mycli-mcp",
        description="MCP server for mycli. Defaults to remote (Streamable HTTP).",
    )
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Run on stdio (local child-process transport). Fallback for editor integrations.",
    )
    parser.add_argument("--host", default=cfg.host, help=f"HTTP bind host (default: {cfg.host}).")
    parser.add_argument("--port", type=int, default=cfg.port, help=f"HTTP bind port (default: {cfg.port}).")
    parser.add_argument(
        "--path",
        default=cfg.path,
        help=f"Streamable HTTP endpoint path (default: {cfg.path}).",
    )
    parser.add_argument(
        "--stateless",
        action="store_true",
        default=cfg.stateless,
        help="Stateless HTTP mode (no per-session state on the server).",
    )
    return parser.parse_args()


def main() -> None:
    """Run the MCP server.

    Default transport: **Streamable HTTP** (remote). Pass ``--stdio`` to fall
    back to a local stdio child-process transport.
    """
    args = _parse_args()

    use_stdio = args.stdio or os.environ.get("MCP_TRANSPORT", "").lower() == "stdio"
    if use_stdio:
        log.info("Starting MCP server on stdio (fallback transport)")
        mcp.run(transport="stdio")
        return

    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.settings.streamable_http_path = args.path
    mcp.settings.stateless_http = args.stateless
    log.info(
        "Starting MCP server on Streamable HTTP at http://{}:{}{}",
        args.host,
        args.port,
        args.path,
    )
    mcp.run(transport="streamable-http")
