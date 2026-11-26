# mcp-template

<p align="center">
  <img src="media/banner.png" alt="mcp-template" width="400">
</p>

<p align="center">
<b>Batteries-included Python template. One codebase ships as a CLI, an MCP server, and an HTTP API over a shared service registry.</b>
</p>

<p align="center">
  <a href="#how-it-works">How it works</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#add-a-feature">Add a feature</a> •
  <a href="#mcp-apps">MCP Apps</a> •
  <a href="#stack">Stack</a> •
  <a href="manual_docs/deploy.md">Deploy</a>
</p>

<p align="center">
  <a href="https://railway.com/deploy/gmailmcp"><img alt="Deploy on Railway" src="https://railway.com/button.svg" height="32"></a>
  &nbsp;
  <a href="https://render.com/deploy?repo=https://github.com/Miyamura80/MCP-Template"><img alt="Deploy to Render" src="https://render.com/images/deploy-to-render-button.svg" height="32"></a>
</p>

<p align="center">
  <img alt="Project Version" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FMiyamura80%2FMCP-Template%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue">
  <img alt="Python Version" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FMiyamura80%2FMCP-Template%2Fmain%2Fpyproject.toml&query=%24.project['requires-python']&label=python&logo=python&color=blue">
  <img alt="GitHub repo size" src="https://img.shields.io/github/repo-size/Miyamura80/MCP-Template">
  <img alt="GitHub Actions Workflow Status" src="https://img.shields.io/github/actions/workflow/status/Miyamura80/MCP-Template/a_test_target_tests.yml?branch=main">
  <a href="https://skills.sh/Miyamura80/MCP-Template"><img alt="skills.sh" src="https://skills.sh/b/Miyamura80/MCP-Template"></a>

</p>

---

## How it works

<p align="center">
  <img src="media/architecture.svg" alt="Write your logic once; it ships as an MCP server, an HTTP API and a CLI" width="880">
</p>

Write your logic once. It ships as an MCP server and an HTTP API automatically, plus a CLI.

## Quick Start

```bash
make onboard        # setup
uv run mymcp-serve  # API + MCP on one port
```

One-click deploy: [Railway](https://railway.com/deploy/gmailmcp) · [Render](https://render.com/deploy?repo=https://github.com/Miyamura80/MCP-Template) · [docs](manual_docs/deploy.md)

## Add a feature

<p align="center">
  <img src="media/add-feature.svg" alt="Define inputs and outputs, write the logic; MCP and API follow, CLI optional" width="880">
</p>

## MCP Apps

<p align="center">
  <img src="media/mcp-apps.svg" alt="MCP-only extras: forms, images and interactive apps in the chat" width="880">
</p>

Add forms, images or an interactive app inside the chat, on MCP only. [Design notes](mcp_server/MCP_UI_ARCHITECTURE.md)

## Stack

| | |
|---|---|
| Interfaces | Typer · MCP Python SDK · FastAPI |
| Auth & billing | WorkOS · Stripe · x402 |
| Data | SQLAlchemy · Alembic |
| LLM | DSPY · LiteLLM · LangFuse |

## About the Core Contributors

<a href="https://github.com/Miyamura80/MCP-Template/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=Miyamura80/MCP-Template" />
</a>

Made with [contrib.rocks](https://contrib.rocks).
