"""No-auth demo MCP mount (``/mcp-demo``).

A second, unauthenticated FastMCP instance serving the curated Gmail tool
surface against canned fixture data, so a visitor can paste one URL into any
MCP client and get a working tool call - and the interactive inbox/composer
MCP Apps - without OAuth or a Google account.

Mutations are simulated: they validate input and return realistic success
responses, but nothing reaches a real mailbox and (drafts aside) nothing
persists between calls. See ``mcp_server/demo/server.py``.
"""
