# cli-template

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

pip install miyamura80-cli-template

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

Opinionated Python template for fast development. Three transports, one shared service layer.

**Transports**
- Typer **CLI** with auto-discovery commands, global flags, output formatting, shell completions, and self-update
- **MCP server** (FastMCP, stdio) - every service is auto-registered as a tool
- **FastAPI HTTP server** with WorkOS + API-key auth, Stripe billing, and rate limiting

**Infra**
- `uv` dependency management + Pydantic-settings config (YAML + `.env`)
- SQLAlchemy + Alembic (`db/`)
- DSPY + LiteLLM with LangFuse observability (`utils/llm/`)
- pytest with `TestTemplate` config-isolation base class
- Ruff + Vulture + ty + import-linter via `make ci`
- prek pre-commit hooks (folder-size guards, ai-writing-check, agent-config sync)
- Ralph Wiggum agent loop, Claude/Codex agent + skill bundles

## Architecture

One codebase, three interfaces. Write business logic once in `services/` and it ships as a CLI subcommand, an MCP tool, and an HTTP route - same Pydantic input/output contract everywhere.

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  cli.py      │  │ mcp_server/  │  │ api_server/  │   transport / interface
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

## Quick Start

```bash
make onboard              # interactive setup (rename, deps, env, hooks)
uv sync                   # install deps
uv run mycli --help       # see all CLI commands
uv run mycli greet Alice  # run a command
uv run mycli init my_command  # scaffold a new command

uv run mycli-mcp          # start the MCP server (stdio)
uv run mycli-api          # start the FastAPI HTTP server
```

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
uv run mycli --format json config show     # JSON output
uv run mycli --dry-run greet Bob           # preview without executing
uv run mycli --verbose greet Alice         # detailed output
```

## Adding Commands

Drop a Python file in `commands/` and it is auto-discovered.

**Single command** - export a `main()` function:

```python
# commands/hello.py
from typing import Annotated
import typer

def main(name: Annotated[str, typer.Argument(help="Who to greet.")]) -> None:
    """Say hello."""
    typer.echo(f"Hello, {name}!")
```

```bash
uv run mycli hello World   # Hello, World!
```

**Subcommand group** - export `app = typer.Typer()`:

```python
# commands/db.py
import typer

app = typer.Typer()

@app.command()
def migrate() -> None:
    """Run migrations."""
    ...
```

```bash
uv run mycli db migrate
```

Or scaffold with: `uv run mycli init my_command --desc "Does something"`.

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
uv run mycli config show                           # full config
uv run mycli config get llm_config.cache_enabled   # single value
uv run mycli config set logging.verbose false      # write override
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
