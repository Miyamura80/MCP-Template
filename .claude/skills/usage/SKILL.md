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

The MCP server exposes the same services as CLI tools via the Model Context
Protocol. **Remote-first**: it defaults to **Streamable HTTP** so it can be
hosted and reached over the network. Stdio is a fallback for local editor
integrations.

```bash
# Default: remote Streamable HTTP server (binds to 127.0.0.1:8765/mcp)
mycli-mcp

# Override host / port / path
mycli-mcp --host 0.0.0.0 --port 9000 --path /mcp

# Fallback: stdio transport for local editor integrations
mycli-mcp --stdio

# Debug with the MCP inspector
mcp dev mcp_server/server.py
```

### Connecting to a remote MCP server (recommended)

Most MCP-aware Hosts accept a URL for Streamable HTTP servers. Point them at
the server's `/mcp` endpoint, e.g. `https://mycli.example.com/mcp`.

### Connecting via stdio (fallback for local editors)

If your editor only supports stdio, add to your MCP client config (e.g.
`.mcp.json`):

```json
{
  "mcpServers": {
    "mycli": {
      "command": "mycli-mcp",
      "args": ["--stdio"]
    }
  }
}
```

## Updating

```bash
mycli update    # check for updates and upgrade
```
