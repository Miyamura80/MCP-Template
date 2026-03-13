# cli-template

<p align="center">
  <img src="media/banner.png" alt="2" width="400">
</p>

<p align="center">
<b>Batteries-included Python CLI template. Auto-discovery commands, global flags, output formatting, self-update, and a whole lot more.</b>
</p>

<p align="center">
  <a href="#key-features">Key Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#cli-usage">CLI Usage</a> •
  <a href="#adding-commands">Adding Commands</a> •
  <a href="#configuration">Configuration</a> •
  <a href="#credits">Credits</a> •
  <a href="#about-the-core-contributors">About the Core Contributors</a>
</p>

<p align="center">
  <img alt="Project Version" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FMiyamura80%2FCLI-Template%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue">
  <img alt="Python Version" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FMiyamura80%2FCLI-Template%2Fmain%2Fpyproject.toml&query=%24.project['requires-python']&label=python&logo=python&color=blue">
  <img alt="GitHub repo size" src="https://img.shields.io/github/repo-size/Miyamura80/CLI-Template">
  <img alt="GitHub Actions Workflow Status" src="https://img.shields.io/github/actions/workflow/status/Miyamura80/CLI-Template/a_test_target_tests.yml?branch=main">

</p>

---

<p align="center">
  <img src="media/cli_demo.gif" alt="CLI Demo" width="600">
</p>


## Key Features

Opinionated Python CLI template for fast development. The `saas` branch extends `main` with web framework, auth, and payments.

| Feature | `main` | `saas` |
|---------|:------:|:------:|
| Auto-discovery command system | ✅ | ✅ |
| Interactive fallback prompts | ✅ | ✅ |
| Shell completions | ✅ | ✅ |
| Self-update | ✅ | ✅ |
| Anonymous telemetry with opt-out | ✅ | ✅ |
| UV + Pydantic config | ✅ | ✅ |
| CI/Linters (Ruff, Vulture) | ✅ | ✅ |
| Pre-commit hooks (prek) | ✅ | ✅ |
| LLM (DSPY + LangFuse Observability) | ✅ | ✅ |
| FastAPI + Uvicorn | ❌ | ✅ |
| SQLAlchemy + Alembic | ❌ | ✅ |
| Auth (WorkOS + API keys) | ❌ | ✅ |
| Payments (Stripe) | ❌ | ✅ |
| Ralph Wiggum Agent Loop | ✅ | ✅ |

[Full comparison](manual_docs/branch_comparison.md)

## Quick Start

```bash
make onboard              # interactive setup (rename, deps, env, hooks)
uv sync                   # install deps
uv run mycli --help       # see all commands
uv run mycli greet Alice  # run a command
uv run mycli init my_command  # scaffold a new command
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

<a href="https://github.com/Miyamura80/CLI-Template/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=Miyamura80/CLI-Template" />
</a>

Made with [contrib.rocks](https://contrib.rocks).
