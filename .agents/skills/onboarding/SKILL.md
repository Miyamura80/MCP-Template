---
name: onboarding
description: Interview the user, inspect this template repo, run headless onboarding, and prune unused systems so a new project gets running quickly.
---

# Onboarding

Use this skill when the user wants to turn this template into a real project,
especially when they invoke `/onboarding`, ask to run onboarding, or want to
remove unused template systems.

## Workflow

1. Inspect the repo before changing anything:
   - `AGENTS.md` or `CLAUDE.md`
   - `pyproject.toml`
   - `Makefile`
   - `README.md`
   - `init/onboard.py`
   - service registry, API server, MCP server, and relevant tests

2. Interview the user briefly. Prefer grouped multi-select questions:
   - Project shape: CLI only, local MCP, remote MCP/API, full SaaS, or custom
   - Service surfaces: CLI commands, MCP tools, HTTP API service routes, MCP Apps/enhancers
   - Infra: auth, database, docs
   - Payments: Stripe billing, Stripe metering/quotas, x402, MPP/ACP stubs, or none
   - Examples: Gmail/Google OAuth app, agentic payment research docs, or none

3. Use `make onboard` as the source of truth:
   - Prefer headless/profile mode when available.
   - Start with `make onboard PROFILE=<profile> DRY_RUN=1`.
   - Use `CONFIG=<path>` for a generated YAML/JSON config when the user selected a custom shape.
   - Only run non-dry onboarding after the user confirms the resolved plan.

4. Apply dependency implications before pruning:
   - Stripe billing needs HTTP API, auth, and database.
   - Stripe metering currently implies Stripe billing.
   - x402 currently needs HTTP API and auth scopes.
   - Gmail/Google OAuth is an example app; it needs API, MCP Apps, auth, and DB.
   - MCP Apps imply the MCP tool surface.

5. Prune deterministically through onboarding code, not ad hoc shell deletes:
   update imports, dependencies, scripts, tests, docs, env examples, CI config, and
   pyproject package lists in the same pass as file removal.

6. Verify the selected shape:
   - CLI: run the CLI help and one simple command.
   - API/MCP: import the server, check `/health`, and confirm `/mcp` if kept.
   - Shared changes: run focused tests first, then broader checks when scope is large.

## Guardrails

- Do not delete auth, DB, API, MCP, docs, payment code, or example apps without explicit user confirmation.
- Do not push to `main`, force-push, or run destructive git commands.
- Treat MCP behavior as current-spec-sensitive; verify current docs before designing new MCP semantics.
- When editing shared skills or agents, run `make sync-agent-config`.
- Keep Gmail/Google OAuth framed as an example integration, not core template infrastructure.
