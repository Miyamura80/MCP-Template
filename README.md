# mcp-template

<p align="center">
  <img src="media/banner.png" alt="2" width="400">
</p>

<p align="center">
<b>Batteries-included Python template. One codebase ships as a CLI, an MCP server, and an HTTP API over a shared service registry.</b>
</p>

<p align="center">
  <a href="#key-features">Key Features</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#cli-usage">CLI Usage</a> •
  <a href="#adding-commands">Adding Commands</a> •
  <a href="#configuration">Configuration</a> •
  <a href="#credits">Credits</a>
</p>

<p align="center">
  <img alt="Project Version" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FMiyamura80%2FMCP-Template%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue">
  <img alt="Python Version" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FMiyamura80%2FMCP-Template%2Fmain%2Fpyproject.toml&query=%24.project['requires-python']&label=python&logo=python&color=blue">
  <img alt="GitHub repo size" src="https://img.shields.io/github/repo-size/Miyamura80/MCP-Template">
  <img alt="GitHub Actions Workflow Status" src="https://img.shields.io/github/actions/workflow/status/Miyamura80/MCP-Template/a_test_target_tests.yml?branch=main">

</p>

---

## Agent Prompt

> Copy and paste this into your AI coding agent (Claude Code, Cursor, Copilot, etc.) to install:

```text
Install the CLI and download the usage skill:

pip install mcp-template

curl -fsSL https://raw.githubusercontent.com/Miyamura80/MCP-Template/main/scripts/install-skills.sh -o install-skills.sh
bash install-skills.sh && rm install-skills.sh
```

---

<p align="center">
  <img src="media/cli_demo.gif" alt="CLI Demo" width="600">
</p>

## App Distribution

- MCP server with OAuth
- Claude and ChatGPT connectors
- APIs and SDKs
- Chat interfaces like iMessage and WhatsApp
- A dashboard that uses the same MCP layer
- Open source

## Key Features

| Feature | Stack |
|---|---|
| CLI (auto-discovery commands, global flags, shell completions, self-update) | Typer |
| MCP server (streamable HTTP at `/mcp`, services auto-registered as tools; stdio supported for local dev) | FastMCP |
| HTTP API server (also hosts `/mcp`) | FastAPI + Uvicorn |
| Auth | WorkOS + API keys |
| Payments | Stripe |
| Database + migrations | SQLAlchemy + Alembic |
| Config (YAML + `.env`) | Pydantic-settings |
| LLM inference + observability | DSPY + LiteLLM + LangFuse |
| Testing | pytest + `TestTemplate` |
| Lint / type / dead-code | Ruff + Vulture + ty + import-linter |
| Pre-commit (folder size, ai-writing, agent-config sync) | prek |
| Agent loop | Ralph Wiggum |
| Telemetry | Anonymous, opt-out |

## Architecture

One codebase, three interfaces. Write business logic once in `services/` and it ships as a CLI subcommand, an MCP tool, and an HTTP route - same Pydantic input/output contract everywhere.

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ src/cli/app  │  │ mcp_server/  │  │ api_server/  │   transport / interface
│  (Typer)     │  │ (FastMCP)    │  │ (FastAPI)    │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       └─────────────────┼─────────────────┘
                         ▼
                 ┌───────────────┐
                 │  services/    │   pure @service functions
                 │  @service     │   (transport-agnostic)
                 └───────┬───────┘
                         ▼
                 ┌───────────────┐
                 │  models/      │   Pydantic I/O contracts
                 └───────┬───────┘
                         ▼
        ┌────────────┬───────┬────────────┬─────────────┐
        │ common/    │ db/   │ utils/llm/ │ src/utils/  │   shared infra
        │ (config)   │ (ORM) │ (DSPY)     │ (logs/theme)│
        └────────────┴───────┴────────────┴─────────────┘
```

### MCP UI (optional)

Need elicitation, image output, or an iframe dashboard for an MCP tool? Add an opt-in **enhancer** in `mcp_server/enhancers/`. Enhancers wrap a service for the MCP transport only - the pure service stays untouched and CLI/API consumers are unaffected.

See [`mcp_server/MCP_UI_ARCHITECTURE.md`](mcp_server/MCP_UI_ARCHITECTURE.md) for the full design.

## Quick Start

```bash
make onboard              # interactive setup (rename, deps, env, hooks)
uv sync                   # install deps
uv run mymcp --help       # see all CLI commands
uv run mymcp greet Alice  # run a command
uv run mymcp init my_command  # scaffold a new command

uv run mymcp-serve        # start the server (HTTP API + MCP at /mcp on one port)
uv run mymcp-mcp          # legacy: stdio MCP only, for local Claude Desktop / dev
```

## One-click deploy

Both targets provision the backend (FastAPI + MCP at `/mcp`) as a Docker service plus a managed Postgres database, run Alembic migrations, and prompt for the required secrets.

### Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Miyamura80/MCP-Template)

Driven by [`render.yaml`](render.yaml). The database and `SESSION_SECRET_KEY` are wired automatically; you're prompted for the WorkOS and Google OAuth secrets. After the first deploy, set `MCP_PUBLIC_URL` to `https://<your-render-host>/mcp` and `GOOGLE_REDIRECT_URI` to `https://<your-render-host>/api/v1/auth/google/callback` (also add that callback to your Google OAuth client), then redeploy.

### Railway

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/ihRiyZ?referralCode=YbnX2i&utm_medium=integration&utm_source=template&utm_campaign=generic)

The committed [`railway.json`](railway.json) pins the Docker build, pre-deploy migrations, and health check, so the template inherits them. To re-generate or update the template: **project Settings → Generate Template from Project → Publish** (dashboard only; the CLI can't publish templates).

<details>
<summary><strong>Forking the Railway template? Variable map for the backend service</strong></summary>

The template has two services: a **Postgres** service (use Railway's standard Postgres defaults) and the **backend**. Set the backend's variables as follows.

**Auto-resolve / auto-generate** (paste as defaults so it deploys with zero input):

| Variable | Value |
|---|---|
| `DEV_ENV` | `prod` |
| `BACKEND_DB_URI` | `${{Postgres.DATABASE_URL}}` (private URL; matches your Postgres service name) |
| `MCP_PUBLIC_URL` | `https://${{RAILWAY_PUBLIC_DOMAIN}}/mcp` |
| `GOOGLE_REDIRECT_URI` | `https://${{RAILWAY_PUBLIC_DOMAIN}}/api/v1/auth/google/callback` |
| `SESSION_SECRET_KEY` | `${{ secret(32) }}` |
| `GOOGLE_TOKEN_ENC_KEY` | `${{ secret(43, "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_") }}=` |

The trailing `=` on `GOOGLE_TOKEN_ENC_KEY` is literal (outside the `${{ }}`): 43 base64url chars + `=` decodes to the 32 bytes Fernet requires. Railway generates `secret(...)` values **once** and persists them, so redeploys don't invalidate the session secret or break stored Gmail tokens.

**Leave empty; the deployer must paste their own** (per-deployment credentials that can't be pre-baked):

`WORKOS_CLIENT_ID` · `WORKOS_API_KEY` · `WORKOS_AUTHKIT_DOMAIN` · `GOOGLE_CLIENT_ID` · `GOOGLE_CLIENT_SECRET`

**Gotchas:**

- `DEV_ENV` must be `prod`. The Dockerfile sets it, but an empty template var *overrides* it back to blank, silently disabling prod token encryption and the prod config overlay.
- The backend service needs a **public domain** enabled, or `${{RAILWAY_PUBLIC_DOMAIN}}` resolves to empty and both URLs break.
- After deploy, register the `GOOGLE_REDIRECT_URI` value in your Google Cloud OAuth client's authorized redirect URIs, and point your WorkOS redirect/resource at `MCP_PUBLIC_URL`. (Inherent to OAuth; can't be automated.)

</details>

See [`.env.example`](.env.example) for the full list of optional integrations (LLM keys, Stripe, LangFuse, …).

## CLI Usage

Global flags go **before** the subcommand:

| Flag | Short | Description |
|---|---|---|
| `--verbose` | `-v` | Increase output verbosity |
| `--quiet` | `-q` | Suppress non-essential output |
| `--debug` | | Show full tracebacks on error |
| `--format` | `-f` | Output format: `table`, `json`, `plain` |
| `--dry-run` | | Preview actions without executing |
| `--version` | `-V` | Print version and exit |

```bash
uv run mymcp --format json config show     # JSON output
uv run mymcp --dry-run greet Bob           # preview without executing
uv run mymcp --verbose greet Alice         # detailed output
```

## Adding Commands

Drop a Python file in `src/cli/commands/` and it is auto-discovered.

**Single command** - export a `main()` function:

```python
# src/cli/commands/hello.py
from typing import Annotated
import typer

def main(name: Annotated[str, typer.Argument(help="Who to greet.")]) -> None:
    """Say hello."""
    typer.echo(f"Hello, {name}!")
```

```bash
uv run mymcp hello World   # Hello, World!
```

**Subcommand group** - export `app = typer.Typer()`:

```python
# src/cli/commands/db.py
import typer

app = typer.Typer()

@app.command()
def migrate() -> None:
    """Run migrations."""
    ...
```

```bash
uv run mymcp db migrate
```

Or scaffold with: `uv run mymcp init my_command --desc "Does something"`.

## Configuration

```python
from common import global_config

# Access config values from common/global_config.yaml
global_config.example_parent.example_child

# Access secrets from .env
global_config.OPENAI_API_KEY
```

CLI config inspection:

```bash
uv run mymcp config show                           # full config
uv run mymcp config get llm_config.cache_enabled   # single value
uv run mymcp config set logging.verbose false      # write override
```

[Full configuration docs](manual_docs/configuration.md)

## Credits

This software uses the following tools:
- [Cursor: The AI Code Editor](https://cursor.com)
- [uv](https://docs.astral.sh/uv/)
- [Typer: CLI framework](https://typer.tiangolo.com/)
- [Rich: Terminal formatting](https://rich.readthedocs.io/)
- [prek: Rust-based pre-commit framework](https://github.com/j178/prek)
- [DSPY: Pytorch for LLM Inference](https://dspy.ai/)
- [LangFuse: LLM Observability Tool](https://langfuse.com/)

## About the Core Contributors

<a href="https://github.com/Miyamura80/MCP-Template/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=Miyamura80/MCP-Template" />
</a>

Made with [contrib.rocks](https://contrib.rocks).
