---
paths:
  - "CLAUDE.md"
  - "AGENTS.md"
---

# Editing CLAUDE.md

`CLAUDE.md` is loaded into every Claude Code session in this repo. Every line costs context. Treat it like a hot path: dense, accurate, no fat.

`AGENTS.md` is a symlink to `CLAUDE.md` - editing one edits the other.

## Validation

Before committing, run both (neither is in `make ci`):

- `make agents_validate` - requires these section headings verbatim: **Project Overview**, **Common Commands**, **Architecture**, **Code Style**, **Configuration Pattern**. If a section's content is now redundant, leave the heading and put a one-liner under it.
- `uv run python scripts/check_ai_writing.py` - bans the em-dash character (U+2014). Use hyphens (`-`) instead. Also runs as the `ai-writing-check` prek hook on commit.

## What earns its place

Include a line only if it's all of:
1. **Non-obvious** - not derivable from a directory name, a standard tool, or PEP/ruff defaults.
2. **Non-rot-prone** - won't go stale on the next refactor (avoid file enumerations, "currently we have X, Y, Z" lists).
3. **Non-rediscoverable** - a future Claude can't trivially get it from one `Read` or one `ls`.
4. **Not duplicated elsewhere in the file** - if the same fact appears in Common Commands AND Architecture AND a code block, pick one.

Things that earn their place:
- The project tagline / headline idea (one codebase, three interfaces over a shared service registry).
- Layering rules (top calls down, never the reverse).
- `file_path:line_number` anchors for key entrypoints (e.g. `services/__init__.py:20`).
- Non-standard infra choices (DSPY+LiteLLM+LangFuse stack, YAML+env config overlay).
- Project-specific conventions (commit emoji prefixes, long-running `init/continue/cleanup` pattern).
- Gotchas (deprecated APIs, MCP spec evolves).
- Disambiguations that are easy to get wrong (MCP terminology pointer, MCP Apps definition).

## What to cut

- ASCII architecture diagrams - they belong in `README.md`, not here. Prefer compact bullet layering.
- Restating directory purpose when the name says it (`db/` = SQLAlchemy, `tests/` = pytest, `.github/workflows/` = GitHub Actions).
- Listing files that mirror a directory (`services/config_svc.py, services/doctor_svc.py, ...`) - they rot and `ls services/` is one tool call.
- Implementation walkthroughs (`_register_tools (line 12) imports every services/*.py module then _make_tool (line 23) wraps each ServiceEntry...`). A `file:line` anchor + one sentence is enough; details are rediscoverable in one `Read`.
- Style rules already enforced by ruff (snake_case, double quotes, line length, `list` vs `typing.List`). The `## Code Style` section should just point at `[tool.ruff]` in `pyproject.toml`.
- Boilerplate intros ("This file provides guidance to Claude Code...").
- Sections duplicated by a dedicated section below (e.g. don't explain config-loading in the architecture bullet AND in `## Configuration Pattern`).
- Code-block comments that restate the next line (`# Access secrets from .env` immediately above `global_config.OPENAI_API_KEY`).

Use a content-appropriate commit emoji from the convention list in `CLAUDE.md` - `🔨` for content changes, `✨` for trim/format-only.
