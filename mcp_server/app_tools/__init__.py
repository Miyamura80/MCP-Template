"""App-only tools — callable by MCP App frontends, hidden from the LLM by convention.

Visibility is hinted via `meta={"ui": {"visibility": ["app"]}}`. Per spec this is
not a hard guarantee — some clients may still expose these to the LLM. For hard
isolation, run a separate FastMCP instance (out of scope). See mcp_server/UI_EDGE_CASES.md A4.
"""
