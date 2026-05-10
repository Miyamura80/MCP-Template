# CLAUDE.md

## Project Overview

Super-opinionated Python template that ships **one codebase, three interfaces** (CLI, MCP server, HTTP API) over a shared service registry. Python >= 3.12 required. Uses `uv` for dependency management (not pip).

**Before any other work in this repo, enable prek:** `uv tool install prek && prek install`. Hooks are defined in `prek.toml`.

**MCP terminology:** For nuances around frequently-confused MCP terms (Host vs. Client vs. Server, Tools vs. Resources vs. Prompts, Roots vs. Resources, transports, OAuth pitfalls, etc.), see [`mcp_server/COMMON_TERMS.md`](./mcp_server/COMMON_TERMS.md). Consult it before naming or designing new MCP-related code.

**MCP Apps:** an MCP extension for interactive iframe-sandboxed UIs (HTML resources via `ui://` URIs) embedded in chat clients, with bidirectional `postMessage` / JSON-RPC communication. See [ext-apps](https://github.com/modelcontextprotocol/ext-apps). *Not* a generic word for "MCP application".

**MCP is an actively-evolving spec.** Behaviors change frequently (transports, auth, primitives). Don't rely on training-data assumptions for anything MCP-related - always verify against the current spec via a fresh web search before writing or reviewing MCP code.

## Common Commands

```bash
# Setup
make all            # Sync dependencies

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

Layering (top calls down, never the reverse):

- **Transports:** `src/cli/app.py` (Typer) · `mcp_server/` (FastMCP) · `api_server/` (FastAPI)
- **Services:** `services/` - pure `@service`-decorated functions, transport-agnostic
- **Contracts:** `models/` - Pydantic input/output schemas shared by all transports
- **Infra:** `common/` (config) · `db/` (SQLAlchemy + Alembic) · `utils/llm/` (DSPY) · `src/utils/` (logging, theme, errors)

### Top-level layout

- **`src/cli/app.py`** + **`src/cli/commands/`** - Typer CLI (`mycli`); `src/cli/commands/__init__.py` auto-discovers `src/cli/commands/*.py` and registers them.
- **`mcp_server/`** - FastMCP server (`mycli-mcp` script), **stdio only**. `mcp_server/server.py:9` creates the server and auto-wraps every `ServiceEntry` as a tool. See [`mcp_server/COMMON_TERMS.md`](./mcp_server/COMMON_TERMS.md) before designing MCP code.
- **`api_server/`** - FastAPI HTTP server (`auth/`, `billing/`, `middleware/`, `routes/`).
- **`services/`** - `@service(name=, description=, input_model=, output_model=)`-decorated pure functions (`services/__init__.py:20`).
- **`common/`** - pydantic-settings config.
  - `global_config.yaml` - base; `<name>.yaml` - split configs loaded as root key `<name>`
  - `production_config.yaml` - overlay loaded with high priority when `DEV_ENV=prod`
- **`src/`** - CLI plumbing (`src/cli/`) + shared utils (logging, theme, errors, output).
- **`utils/llm/`** - DSPY + LiteLLM wrapper with fallback model, Tenacity retries, LangFuse observability.
- **`tests/`** - subclass `TestTemplate` (`tests/test_template.py:14`) for per-test config isolation.
- **`docs/`** - Next.js + Fumadocs site; English source in `docs/content/en/`.
- **`.claude/`**, **`.agents/`**, **`.codex/`** - Claude/Codex agents and skills kept in sync by `scripts/sync_agent_config.py` (pre-commit enforced).

**Don't add new files at the repo root** unless tooling requires it. Nest under an existing folder.

### Adding a new feature

1. Pydantic models in `models/<feature>.py`.
2. Pure `@service` function in `services/<feature>_svc.py`.
3. (CLI) Typer command in `src/cli/commands/<feature>.py` calling the service.
4. (MCP) Nothing - `mcp_server/server.py` auto-registers on import.
5. (HTTP, optional) Route in `api_server/routes/`.
6. Tests inheriting `TestTemplate`.

## Code Style

Enforced by ruff - see `[tool.ruff]` in `pyproject.toml`. Run `make fmt` and `make ruff`.

## Configuration Pattern

```python
from common import global_config

global_config.example_parent.example_child
global_config.llm_config.default_model
global_config.OPENAI_API_KEY  # secrets from .env
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

## MCP: Headless vs UI

This template supports two MCP tool styles:

- **Headless tools** (default) - sync wrapper, no `Context`, returns the
  Pydantic output model so FastMCP derives `outputSchema`. The CLI/API/MCP
  transports share identical behavior. Use this for any tool the LLM should
  call autonomously without UI affordances.
- **Enhanced tools** (opt-in via `@enhance` in `mcp_server/enhancers/`) - async
  wrapper with `Context`, may elicit user input mid-call, attach images/audio,
  or render an MCP App (iframe dashboard). MCP-only - never affects CLI/API
  consumers of the same service. The pure service stays untouched in
  `services/`.

If a tool just returns data, leave it headless. Reach for an enhancer only
when the MCP transport needs something the spec offers and other transports
don't.

### Adding an enhancer

```python
from mcp_server.enhancers import enhance
from mcp_server.enhancers.base import EnhancedTool

@enhance("my_service", fallback="headless")
async def my_enhanced(tool: EnhancedTool[MyInput, MyOutput]) -> MyOutput:
    result = tool.call()
    if tool.can_elicit:
        ...  # await tool.elicit(...)
    if tool.can_show_app:
        tool.send_app("ui://mycli/my_dashboard")
    return result
```

Then add `import mcp_server.enhancers.my_service  # noqa: F401` to
`_register_tools()` in `mcp_server/server.py`.

### MCP Apps (iframe dashboards)

Apps live in `mcp_server/apps/<name>/`:
- React + Vite + `vite-plugin-singlefile` (always bun, never npm)
- `dist/mcp-app.html` is **committed** so the template works without Node
- `make build_apps` rebuilds the bundle (developer-only; not part of CI)
- `make dev_host` runs the upstream `@modelcontextprotocol/ext-apps` basic-host
  for manual smoke testing

When adding a new app, also add an entry to `[tool.hatch.build.targets.wheel]
force-include` in `pyproject.toml` so the HTML ships in the wheel.

See `mcp_server/MCP_UI_ARCHITECTURE.md` for design rationale and
`mcp_server/MCP_UI_EDGE_CASES.md` for the edge-case spec.

## Subagents

- Folder-size CI failure → spawn subagent `.claude/agents/folder-refactor-advisor.md`.

## Git Workflow
- **Protected Branch**: `main` is protected. Do not push directly to `main`. Use PRs.
- **Merge Strategy**: Squash and merge.
- **Never force push**: Do not use `git push --force` or `--force-with-lease`. If you hit a git issue, stop and ask the user for guidance.
- **Pre-commit CI gate**: Always run `make ci` before committing any changes. Ensure it passes with zero errors. Do not commit if `make ci` fails - fix all issues first, then commit.

## Deprecated

- Don't use `datetime.utcnow()` - use `datetime.now(timezone.utc)`
