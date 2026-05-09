# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Super-opinionated Python template that ships **one codebase, three interfaces** (CLI, MCP server, HTTP API) over a shared service registry. Python >= 3.12 required. Uses `uv` for dependency management (not pip).

The headline idea: write business logic once as a pure function in `services/`, register it with `@service`, and it shows up as a Typer CLI subcommand, an MCP tool, and (where wired) a FastAPI route — with the same Pydantic input/output contract everywhere.

**Before any other work in this repo, enable prek:** `uv tool install prek && prek install`. Hooks are defined in `prek.toml`.

**MCP terminology:** For nuances around frequently-confused MCP terms (Host vs. Client vs. Server, Tools vs. Resources vs. Prompts, Roots vs. Resources, transports, OAuth pitfalls, etc.), see [`mcp_server/COMMON_TERMS.md`](./mcp_server/COMMON_TERMS.md). Consult it before naming or designing new MCP-related code.

**MCP Apps:** an MCP extension for interactive iframe-sandboxed UIs (HTML resources via `ui://` URIs) embedded in chat clients, with bidirectional `postMessage` / JSON-RPC communication. See [ext-apps](https://github.com/modelcontextprotocol/ext-apps). *Not* a generic word for "MCP application".

**MCP is an actively-evolving spec.** Behaviors change frequently (transports, auth, primitives). Don't rely on training-data assumptions for anything MCP-related - always verify against the current spec via a fresh web search before writing or reviewing MCP code.

## Common Commands

```bash
# Onboarding & Setup
make onboard        # Interactive onboarding CLI (rename, deps, env, hooks, media)
make all            # Sync deps and run main.py

# Testing
make test           # Run pytest on tests/
make test_fast      # Run fast tests (no slow/nondeterministic)
make test_flaky     # Repeat fast tests to detect flakiness
make test_slow      # Run slow tests only
make test_nondeterministic # Run nondeterministic tests only

# Code Quality (run after major changes)
make fmt            # Run ruff formatter + JSON formatting
make ruff           # Run ruff linter
make vulture        # Find dead code
make ty             # Run type checker
make lint_links     # Check for broken links in markdown files (README, etc.)
make ci             # Run all CI checks (ruff, vulture, ty, import_lint, docs_lint, check_deps, lint_links)

# Dependencies
uv sync             # Install dependencies (not pip install)
uv add <pkg>        # Add new dependency
uv run python <file> # Run Python files
uv run pytest path/to/test.py  # Run specific test

# Release
# 1. Update version in pyproject.toml
# 2. Tag the commit: git tag -a v0.1.0 -m "Release v0.1.0"
# 3. Push the tag: git push origin v0.1.0 (triggers Release workflow)
```

## Architecture

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  cli.py      │  │ mcp_server/  │  │ api_server/  │   transport / interface layer
│  (Typer)     │  │ (FastMCP)    │  │ (FastAPI)    │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       └─────────────────┼─────────────────┘
                         ▼
                 ┌───────────────┐
                 │  services/    │   pure business logic, transport-agnostic
                 │  @service     │   (registered into a shared registry)
                 └───────┬───────┘
                         │   I/O contracts
                 ┌───────▼───────┐
                 │  models/      │   Pydantic input/output models
                 └───────┬───────┘
                         ▼
        ┌────────────┬───────┬────────────┬─────────────┐
        │ common/    │ db/   │ utils/llm/ │ src/utils/  │   shared infra
        │ (config)   │ (ORM) │ (DSPY)     │ (logs/theme)│
        └────────────┴───────┴────────────┴─────────────┘
```

### Top-level layout

- **`cli.py`** + **`commands/`** — Typer CLI entrypoint (`mycli`). `cli.py:181 main_cli` is the console script; `commands/__init__.py` auto-discovers subcommands from `commands/*.py` (config, doctor, greet, secrets) and registers them on the Typer app.
- **`mcp_server/`** — FastMCP server exposed as the `mycli-mcp` console script. `mcp_server/server.py:9` creates `FastMCP("mycli")`; `_register_tools` (line 12) imports every `services/*.py` module to populate the registry, then `_make_tool` (line 23) wraps each `ServiceEntry` as an MCP tool by synthesizing a signature from its Pydantic input model. Transport is **stdio only**. See [`mcp_server/COMMON_TERMS.md`](./mcp_server/COMMON_TERMS.md) before naming/designing MCP code.
- **`api_server/`** — FastAPI HTTP server with `auth/`, `billing/`, `middleware/`, `routes/`. Independent of the MCP server.
- **`services/`** — **The core abstraction.** Pure functions decorated with `@service(name=, description=, input_model=, output_model=)` (see `services/__init__.py:20`). They take a Pydantic input model and return a Pydantic output model — no logging, no transport concerns. Currently: `config_svc.py`, `doctor_svc.py`, `greet.py`.
- **`models/`** — Pydantic input/output schemas referenced by services (`auth.py`, `config.py`, `doctor.py`, `greet.py`).
- **`common/`** — Global configuration via pydantic-settings.
  - `global_config.yaml` — base config (LLM defaults, logging, features, server)
  - `<name>.yaml` — optional split configs, loaded as root key `<name>` (e.g. `payments.yaml`, `subscription_config.yaml`)
  - `production_config.yaml` — overlay loaded with high priority when `DEV_ENV=prod`
  - `global_config.py:39` — custom `YamlSettingsSource` that merges YAMLs + `.env`
  - `config_models.py` — typed `BaseSettings` model classes
  - Access via `from common import global_config`
- **`db/`** — SQLAlchemy `base.py` + `engine.py`, ORM `models/`, Alembic `migrations/`. Driven by `alembic.ini` at repo root.
- **`src/`** — CLI plumbing (`src/cli/state.py` — Verbosity/OutputFormat contextvars; `src/cli/telemetry.py`, `src/cli/security.py`, `src/cli/scaffold.py`, `src/cli/update.py`, `src/cli/completions.py`) and `src/utils/` (logging_config, theme, errors, interactive, progress, output).
- **`utils/llm/`** — `dspy_inference.py` wraps DSPY + LiteLLM with a fallback model and Tenacity retries; `dspy_langfuse.py` adds LangFuse observability.
- **`tests/`** — pytest. Subclass `TestTemplate` (`tests/test_template.py:14`) which deep-copies `global_config` per test, marks `test=True`, and exposes config keys as `self.*`. Markers (`slow_test`, `nondeterministic_test`, `e2e_test`) are registered in `tests/conftest.py`.
- **`init/`** — one-time brand-asset generators (banner, logo) used by `make banner` / `make logo`.
- **`onboard.py`** + **`make onboard`** — interactive Typer wizard that renames the project (kebab-case → `pyproject.toml [project].name`, `[project.scripts]`, config), wires `.env`, runs `uv sync`, installs `prek`, and generates media.
- **`docs/`** — Next.js + Fumadocs site. English source in `docs/content/en/`; `docs/content/{es,ja,zh}/` is generated by the **Jules Translation Sync** workflow — never edit translations by hand. See `docs/translation-guide.md`.
- **`.claude/`**, **`.agents/`**, **`.codex/`** — Claude Code and Codex agent/skill definitions kept in sync by `scripts/sync_agent_config.py` (run via `make sync-agent-config`, also enforced by a pre-commit hook).
- **`.github/workflows/`** — CI (tests, ruff/ty/vulture, folder-size, large-files, codeql), release on tag, Jules translation sync, branch cleanup.

### Adding a new feature (the canonical flow)

1. Define input/output Pydantic models in `models/<feature>.py`.
2. Write a pure function in `services/<feature>_svc.py` decorated with `@service(...)`.
3. (CLI) Add a Typer command in `commands/<feature>.py` that calls the service.
4. (MCP) Nothing to do — `mcp_server/server.py` auto-registers it as a tool on import.
5. (HTTP, optional) Add a route in `api_server/routes/` that calls the service.
6. Add tests under `tests/` inheriting `TestTemplate`.

Keep services free of logging, I/O, and transport concerns. Anything that needs side effects belongs in the caller.

## Code Style

- snake_case for functions/files/directories
- CamelCase for classes
- UPPERCASE for constants
- 4-space indentation, double quotes
- Use built-in types (list, dict, tuple) not typing.List/Dict/Tuple

## Configuration Pattern

```python
from common import global_config

# Access config values
global_config.example_parent.example_child
global_config.llm_config.default_model

# Access secrets from .env
global_config.OPENAI_API_KEY
```

## LLM Inference Pattern

```python
from utils.llm.dspy_inference import DSPYInference
import dspy

class MySignature(dspy.Signature):
    input_field: str = dspy.InputField()
    output_field: str = dspy.OutputField()

inf_module = DSPYInference(pred_signature=MySignature, observe=True)
result = await inf_module.run(input_field="value")
```

## Testing Pattern

```python
from tests.test_template import TestTemplate
from tests.conftest import slow_test, nondeterministic_test

class TestMyFeature(TestTemplate):
    def test_something(self):
        assert self.config is not None

    @slow_test
    def test_slow_operation(self):
        pass
```

## Logging

```python
from loguru import logger as log
from src.utils.logging_config import setup_logging

setup_logging()
log.debug("detailed diagnostic information")
log.info("general informational message")
log.warning("warning message for potentially harmful situations")
log.error("error message for error events")
```

## Commit Message Convention

Use emoji prefixes indicating change type and magnitude (multiple emojis = 5+ files):
- 🏗️ initial implementation
- 🔨 feature changes
- 🐛 bugfix
- ✨ formatting/linting only
- ✅ feature complete with E2E tests
- ⚙️ config changes
- 💽 DB schema/migrations

## Long-Running Code Pattern

Structure as: `init()` → `continue(id)` → `cleanup(id)`
- Keep state serializable
- Use descriptive IDs (runId, taskId)
- Handle rate limits, timeouts, retries at system boundaries

## Subagents

- Folder-size CI failure → spawn subagent `.claude/agents/folder-refactor-advisor.md`.

## Git Workflow
- **Protected Branch**: `main` is protected. Do not push directly to `main`. Use PRs.
- **Merge Strategy**: Squash and merge.
- **Never force push**: Do not use `git push --force` or `--force-with-lease`. If you hit a git issue, stop and ask the user for guidance.
- **Pre-commit CI gate**: Always run `make ci` before committing any changes. Ensure it passes with zero errors. Do not commit if `make ci` fails - fix all issues first, then commit.

## Deprecated

- Don't use `datetime.utcnow()` - use `datetime.now(timezone.utc)`

---

## Automated Translation (Jules Sync)

Docs under `docs/content/` are auto-translated by the **Jules Translation Sync**
workflow. Do NOT manually translate doc files - edit the English source and the
workflow will update all locales (`es`, `ja`, `zh`).
See [`docs/translation-guide.md`](docs/translation-guide.md) for the full
glossary, file naming conventions, and translation rules.
