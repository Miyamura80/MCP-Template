---
paths:
  - "CLAUDE.md"
  - "AGENTS.md"
---

# Editing CLAUDE.md

`CLAUDE.md` is loaded into every Claude Code session. Every line costs context. Treat it like a hot path: dense, accurate, no fat.

`AGENTS.md` is often a symlink or copy of `CLAUDE.md` - check before editing; if linked, editing one edits both.

## Validation

Repos commonly enforce structure (required headings, banned characters, link checks) via Make targets, scripts, or pre-commit hooks. Before committing, run whatever the repo defines locally - don't rely on CI to catch it. If the repo has no validation, the hard requirement is just: don't break the section headings other tools rely on.

## What earns its place

Include a line only if it's all of:
1. **Non-obvious** - not derivable from a directory name, a standard tool, or sensible defaults.
2. **Non-rot-prone** - won't go stale on the next refactor (avoid file enumerations, "currently we have X, Y, Z" lists).
3. **Non-rediscoverable** - a future Claude can't trivially get it from one `Read` or one `ls`.
4. **Not duplicated elsewhere in the file** - if the same fact appears in multiple sections or in a code block, pick one.

Categories that typically earn their place:
- The project's headline architecture idea or organizing principle.
- Layering / dependency-direction rules.
- `file:line` anchors for key entrypoints.
- Non-standard infra choices a reader would assume differently by default.
- Project-specific conventions (commit message format, naming patterns, long-running task patterns).
- Gotchas (deprecated APIs, fast-evolving specs, build quirks).
- Disambiguations for terms or concepts that are easy to get wrong.

## What to cut

- ASCII architecture diagrams - they belong in `README.md`. Prefer compact bullet layering.
- Restating directory purpose when the name already says it.
- Listing files that mirror a directory - they rot, and `ls` is one tool call.
- Implementation walkthroughs. A `file:line` anchor + one sentence is enough; details are rediscoverable in one `Read`.
- Style rules already enforced by the linter/formatter. The Code Style section should just point at the linter config.
- Boilerplate intros ("This file provides guidance to Claude Code...").
- Sections duplicated by a dedicated section below.
- Code-block comments that restate the next line.

If the repo defines a commit message convention (emoji prefixes, conventional commits, etc.), follow it for changes to this file.
