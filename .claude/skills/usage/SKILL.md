---
name: usage
description: How to use the CLI, API, and MCP interfaces. Use this skill when interacting with the tool as an end user.
---
# Usage Guide

This skill teaches you how to use the three interfaces provided by this project.

## CLI

```bash
# Install
pip install miyamura80-cli-template

# Basic usage
mycli --help                  # see all commands
mycli greet Alice             # run a command
mycli config show             # view configuration
mycli doctor                  # check system health

# Global flags (go before the subcommand)
mycli --verbose greet Alice   # detailed output
mycli --format json config show  # JSON output
mycli --dry-run greet Bob     # preview without executing
mycli --version               # print version
```

## API

```bash
# Start the API server
mycli-api

# The server runs on http://localhost:8000 by default
# See /docs for the interactive OpenAPI documentation
```

## MCP

The MCP server exposes the same services as CLI tools via the Model Context Protocol.

```bash
# Run directly (stdio transport)
mycli-mcp

# Debug with the MCP inspector
mcp dev mcp_server/server.py
```

### Connecting MCP to your editor

Add to your MCP client config (e.g. `.mcp.json`):

```json
{
  "mcpServers": {
    "mycli": {
      "command": "mycli-mcp"
    }
  }
}
```

## Updating

```bash
mycli update    # check for updates and upgrade
```
