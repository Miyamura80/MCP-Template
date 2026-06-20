"""Export the LLM-facing MCP tool surface to a committed JSON snapshot.

The landing page advertises the server's `tools[]` in its static SEP-2127 card,
but the *source of truth* for that list is the Python `@service` registry - not
hand-maintained marketing config. This script snapshots the registry (the same
list `api_server` serves at `/.well-known/mcp/server-card.json`) into

    landing-page/src/config/tool-surface.generated.json

which `landing-page/scripts/gen-discovery.ts` reads at build time. Run it
whenever you add, remove, or rename a service:

    make gen_tool_surface     # or: uv run python scripts/export_tool_surface.py

The output is committed so the bun/Astro landing build needs no Python at build
time (mirroring the committed `openapi.json` / `server-card.json` snapshots).
"""

from __future__ import annotations

import json
import pathlib
import sys

from mcp_server.server import llm_tool_surface

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "landing-page" / "src" / "config" / "tool-surface.generated.json"


def main() -> int:
    tools = [
        {"name": entry.name, "description": entry.description}
        for entry in llm_tool_surface()
    ]
    OUT_PATH.write_text(json.dumps(tools, indent=2) + "\n", encoding="utf-8")
    print(f"✓ wrote {len(tools)} tools to {OUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
