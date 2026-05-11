---
name: usage
description: How to use the CLI, API, and MCP interfaces. Use this skill when interacting with the tool as an end user.
---
# Usage Guide

This skill teaches you how to use the three interfaces provided by this project.

## CLI

```bash
# Install
pip install mcp-template

# Basic usage
mymcp --help                  # see all commands
mymcp greet Alice             # run a command
mymcp config show             # view configuration
mymcp doctor                  # check system health

# Global flags (go before the subcommand)
mymcp --verbose greet Alice   # detailed output
mymcp --format json config show  # JSON output
mymcp --dry-run greet Bob     # preview without executing
mymcp --version               # print version
```

## API

```bash
# Start the API server
mymcp-api

# The server runs on http://localhost:8000 by default
# See /docs for the interactive OpenAPI documentation
```

## MCP

The MCP server exposes the same services as CLI tools via the Model Context Protocol.

```bash
# Run directly (stdio transport)
mymcp-mcp

# Debug with the MCP inspector
mcp dev mcp_server/server.py
```

### Connecting MCP to your editor

Add to your MCP client config (e.g. `.mcp.json`):

```json
{
  "mcpServers": {
    "mymcp": {
      "command": "mymcp-mcp"
    }
  }
}
```

## Updating

```bash
mymcp update    # check for updates and upgrade
```
