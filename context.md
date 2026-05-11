# Session Context: Remote MCP Server Refactor

**Branch:** `claude/remote-mcp-servers-5G9H9` · **Commit:** `f45ff27`
**Goal:** Make this template a real remote-MCP template (streamable HTTP), not
just a stdio-with-FastAPI-bolted-on.

This document captures the *why* behind every decision. The diff captures the
*what*; this captures the reasoning a future reader would otherwise have to
reverse-engineer.

---

## Why this refactor at all

The repo described itself as "one codebase, three interfaces (CLI / MCP / HTTP
API) over a shared service registry," but the MCP transport was stdio-only.
That made the template good for local Claude Desktop use and almost nothing
else — every modern MCP-host story (Claude.ai, Cursor remote, hosted
deployments) needs streamable HTTP. Treating stdio as primary also fights the
MCP spec's direction: the working group has been steadily de-emphasising
stdio in favour of streamable HTTP for production.

The user opened the session asking for an audit of *implied* design decisions
about local-only operation before any code changes — they explicitly wanted
to surface those assumptions and decide which ones to invert. That framing
ended up driving every subsequent decision.

---

## Decisions and reasoning

### 1 / 1A — Mount FastMCP under FastAPI at `/mcp` (single process)

**Chosen:** one FastAPI app, FastMCP's streamable-HTTP Starlette sub-app
mounted at `/mcp`, one uvicorn process, one port.

**Alternatives rejected:**
- *Two separate processes / ports.* Would have doubled deployment complexity
  (two services on Railway, two URLs, two TLS certs, two sets of env vars)
  for no real win. The two transports already share the entire service
  registry and middleware contract.
- *Subdomain split (`api.` vs `mcp.`).* Same complexity cost as two processes
  plus DNS/cert overhead. Useful only if MCP traffic patterns diverged
  sharply from API traffic, which they don't here.

**Why this wins:** auth, CORS, rate-limiting, request IDs, logging, and
session middleware are all configured once and apply uniformly. Quotas and
billing can be enforced at one layer. A single deploy artifact.

**Hidden cost we accepted:** FastMCP's `streamable_http_app()` is a Starlette
sub-app that needs its session manager started inside an async lifespan. If
you forget to wire `mcp.session_manager.run()` into the parent app's
lifespan, every `/mcp` request fails with `RuntimeError: Task group is not
initialized`. This is documented in FastMCP but easy to miss — we hit it in
Phase 1 smoke testing and had to add `mcp_server.server.lifespan` that the
FastAPI app composes in.

### 2 / 2A — Reuse `unified_auth` for `/mcp`; defer OAuth 2.1

**Chosen:** same Bearer-JWT (WorkOS) + `X-API-KEY` chain that the REST API
uses. OAuth 2.1 dynamic client registration (the MCP-spec-preferred flow)
tracked separately as issue #53.

**Why not do OAuth now:** OAuth 2.1 + DCR for MCP is a meaningfully bigger
piece of work than the rest of this refactor combined. Doing it inline would
have delayed the (much higher-value) "make remote work at all" milestone by
days. The pragmatic version: ship remote-MCP today with the same creds the
REST API already issues; a sophisticated host can use an API key, a less
sophisticated one is no worse off than before.

**Why this is safe:** WorkOS JWT is already verified for the REST routes,
and API keys are already hashed-at-rest with scopes. The `/mcp` middleware
is literally the same code path, just adapted to ASGI.

### 3 — Services stay pure; `current_user()` is a ContextVar

**Chosen:** keep `services/` transport-agnostic. The auth middleware sets a
`ContextVar[AuthenticatedUser | None]` that services *can* read if they
need per-user behaviour, but most won't.

**Alternative rejected:** thread a `user` arg through every service
signature. This would have:
- broken the existing pure-function ergonomics that make CLI commands
  one-liners,
- forced every CLI command to invent a fake user object,
- and required touching every model in `models/` to add an auth field that
  CLI consumers don't care about.

**Why ContextVar specifically:** it's the standard async-safe way to do
per-request implicit state in Python. Works across `await`s, doesn't leak
between requests, doesn't require modifying call sites.

**Trade-off accepted:** services that *do* read `current_user()` are no
longer trivially testable by direct function call without setting the var.
That's an acceptable cost — most services won't need it, and the ones that
do have an obvious "this is per-user" signal in their implementation.

### 4 — Two scripts: `mymcp-serve` primary, `mymcp-mcp` legacy stdio

**Chosen:** `mymcp-serve` is the new primary entrypoint (HTTP + MCP).
`mymcp-mcp` stays available for stdio because **the user explicitly chose
option 10B — keep stdio as a labeled-legacy escape hatch** rather than
delete it.

**Why keep stdio at all:** Claude Desktop config files in the wild already
point at `mymcp-mcp`. Removing the script silently breaks every existing
user. The deprecation banner on stderr is the gentle nudge.

**Why not just rename:** `mymcp-api` was the old HTTP name. Renaming it to
`mymcp-serve` (instead of e.g. `mymcp-http`) reflects the new reality that
this script now serves *both* HTTP and MCP — "serve" is honest, "api" would
be misleading. The old `mymcp-api` is preserved as a deprecated alias
because that name probably appears in some user's docker-compose somewhere.

### 5 — Dynamic service / enhancer / app-tool discovery

**Chosen:** `discover_services()`, `discover_enhancers()`,
`discover_app_tools()` all walk their packages with `pkgutil.iter_modules`.

**Why:** the previous `mcp_server/server.py:_register_tools` had an
ever-growing list of explicit imports (`import services.config_svc  # noqa`,
etc.). Every new service required two edits (add file, add import line),
and the imports always lagged. The REST router (`api_server/routes/services.py`)
already used `pkgutil` for the same problem — so this is just lifting an
existing pattern to be shared.

**Why idempotent:** discovery is now called from multiple entry points
(`build_mcp_server`, the API route loader, tests). A `_discovered` flag
prevents repeated module imports from creating duplicate registrations.

### 6 — Keep `ui://` resources, configure CORS at FastAPI layer

**Chosen:** MCP Apps (the iframe-dashboard extension) still use `ui://`
URIs; CORS is configured once on the parent FastAPI app and inherits to
`/mcp`. The `_meta.ui.domain` value gets filled in by the operator at
deploy time, not hardcoded.

**Why this matters:** MCP Apps embed an iframe in the host chat client.
Modern hosts enforce CSP — `_meta.ui.domain` tells the host which origin
to allow. We can't bake a value in because every deploy has a different
URL.

**What we did not do:** the recent ext-apps proposal to serve streamable
HTML directly (issue #603) is interesting but not stable yet. Staying on
the `text/html;profile=mcp-app` resource model means we don't bet on a
spec that may change.

### 7 — Stateless v1, no Redis

**Chosen:** FastMCP's default streamable-HTTP mode is stateful (each
session has a manager-allocated ID), but we don't add Redis-backed session
storage in v1. Sessions live in-process.

**Why this is fine:** Railway and most other PaaS hosts will run a single
instance until traffic justifies scaling. A single-instance stateful
server is correct without external session storage.

**What we documented as a known limitation:** MCP elicitation (the
spec-defined "ask the user for input mid-tool-call" flow) may not work
reliably over remote streamable HTTP today — the user-facing client has to
support it, and most don't yet. The headless code path is unaffected;
enhancers that use elicitation are the only ones at risk, and they already
have a `fallback="headless"` parameter for exactly this case.

**When to revisit:** when we need horizontal scale (more than one server
instance) we'll need Redis-backed `StreamableHTTPSessionManager`. That's a
follow-up issue.

### 8 / 8A — Dockerfile + railway.json

**Chosen:** ship a Dockerfile that runs `mymcp-serve`, a `railway.json` for
zero-config Railway deploys, a `.dockerignore`, and prod-config placeholders
in `common/production_config.yaml`.

**Why Railway specifically:** the user pointed at Railway as the target.
The MCP servers we'd configured for the session both include Railway tools,
which signals the existing team workflow. But the Dockerfile is generic —
anything that runs a container works.

**Why `DEV_ENV=prod` in the Dockerfile:** the existing config layer picks
up `production_config.yaml` based on this env var. Setting it in the image
means a fresh deploy is automatically prod-mode without an extra step on
the dashboard.

### 9 — One `server` config block

**Chosen:** there's already a `server` block in `global_config.yaml`
(host, port, allowed_origins). We extend it rather than introducing a
separate `mcp` config block.

**Why:** there's no separate MCP server any more. The "server" is one
thing. Two config blocks would have suggested two configurable things and
invited bugs where they drift apart.

### 10 / 10B — Stdio is legacy, but kept

User explicitly chose option B — keep stdio, label as legacy, don't delete.
Reasoning covered in #4 above. The deprecation banner is on stderr (not
stdout) specifically so it can't break stdio JSON-RPC framing.

---

## Surprises and gotchas hit during execution

These weren't obvious from reading the code; they only showed up when we
tried to make it work.

### FastMCP lifespan is required for streamable HTTP

`FastMCP.streamable_http_app()` returns a working Starlette app, but its
internal `StreamableHTTPSessionManager` won't accept requests until its
`run()` async context manager has been entered. FastMCP's docs show this
pattern in the lifespan, but if you mount the sub-app on a parent FastAPI
app, you have to compose the parent's lifespan to include FastMCP's. The
parent app's lifespan is the only one Starlette runs — sub-app lifespans
are silently ignored when mounted.

We caught this with a smoke test that returned 500 with
`Task group is not initialized`. Without that test it would have shipped
broken.

### `mount("/", mcp.streamable_http_app())` is the right pattern

FastMCP's streamable-HTTP app already serves at `/mcp` internally. If you
mount it at `/mcp`, you get `/mcp/mcp`. Mounting at `/` looks weird but is
correct — the only reason it isn't a footgun is that `/api/v1/...` and
`/health` are all defined as routes on the parent app *before* the mount,
so they shadow the sub-app's catch-all.

### FastMCP enables DNS-rebinding protection on localhost

FastMCP auto-enables DNS-rebinding protection when host is `127.0.0.1`,
`localhost`, or `::1` (production-safe default). It checks `Host` header
against an allowlist. FastAPI's TestClient sends `Host: testserver` by
default, which is rejected with 421. The test fix is one line —
`headers={"Host": "127.0.0.1:8080"}` — but the symptom (`Invalid Host
header`) doesn't immediately point at the cause.

### `StreamableHTTPSessionManager.run()` can only be called once

Per FastMCP instance, *per process*. We hit this with `with TestClient(app)`
in two tests: the first test enters the lifespan, runs the session manager,
exits and tears it down; the second test tries to run it again on the same
manager → `RuntimeError`. Fix: use plain `TestClient(app)` (which doesn't
trigger the lifespan) for tests that don't actually exercise the MCP
endpoint, and reserve `with TestClient(app)` for the one test that needs
the live session manager.

### Module-level `mcp` singleton has to stay

The pre-refactor `mcp_server/server.py` defined `mcp = FastMCP("mymcp")` at
module top, and `mcp_server/app_tools/doctor_dashboard.py` did
`from mcp_server.server import mcp` to register a tool with `@mcp.tool`.
First attempt at the refactor moved `mcp` inside `build_mcp_server()` for
cleanliness — broke the app_tools import. Restored the module-level
singleton; `build_mcp_server` mutates it instead of constructing it. Less
elegant, but the alternative would have meant refactoring every
app_tools/enhancer file to defer registration, which is out of scope.

### `ty` is permanently noisy in this repo

Baseline `main` has 16 `ty` diagnostics with the current ty version
(0.0.35). Most are `# type: ignore[...]`-suppressed lines that newer ty
doesn't respect, plus a couple of legitimate Stripe / SQLAlchemy
result-type issues. GitHub Actions doesn't run `make ty` — only `make
ruff`. So `make ci` failing locally on ty is a known false-positive that
won't block the PR.

We left these pre-existing 15 errors alone (we net-removed one by typing
`current_user.py` properly). Trying to fix them all would have been
scope-creep.

### `make lint_links` is also broken in main

Many broken links in `README.md`, `docs/README.md`, `mcp_server/COMMON_TERMS.md`
— all pre-existing, none in files we touched. Same story as `ty`: noisy
locally, not enforced by CI.

### import-linter contract had to change

The existing contract `api_server_no_cli_mcp` forbade `api_server` from
importing `mcp_server`. That was correct under the old architecture (they
were separate processes). Under the new architecture, `api_server`
*explicitly* imports `mcp_server` to mount it. We narrowed the contract to
`api_server_no_cli` (still forbids `src.cli` imports). This is the kind of
contract change that should be flagged in review.

---

## What we explicitly did NOT do

These came up and got deferred on purpose.

- **OAuth 2.1 / dynamic client registration** — tracked in issue #53.
  Real work, real complexity, not blocking remote-MCP MVP.
- **Bump `mcp[cli]` past `<2.0.0`** — `0.x` of FastMCP's streamable-HTTP
  API is what we have; bumping is its own audit. Current pin works.
- **Redis-backed session manager** — not needed for single-instance
  deploys. Add when horizontal scale arrives.
- **Per-tool quota enforcement on `/mcp`** — REST routes call
  `ensure_daily_limit(user_id)` per request. The `/mcp` middleware does
  auth but skips quota because tool invocations are nested inside JSON-RPC
  messages on a streaming connection — enforcing at the right point
  requires parsing JSON-RPC method names. Worth a follow-up but not v1.
- **Rewrite the 8 client tabs in `docs/content/docs/mcp/setup.mdx`** — we
  added a "Remote (recommended)" section at the top and labeled the
  existing per-client tabs as legacy/local. Each tab is still valid for
  stdio use; bulk-rewriting them would have ballooned the diff for low
  marginal value.
- **Open a PR** — explicit instruction: only when the user asks.

---

## What's left for whoever picks this up next

1. **Open the PR** when you're ready, from
   `claude/remote-mcp-servers-5G9H9` → `main`. The commit is `f45ff27`,
   squash-merge per repo policy.
2. **Set Railway env vars** before deploying: `DEV_ENV=prod`,
   `BACKEND_DB_URI`, `SESSION_SECRET_KEY`, and any LLM/Stripe keys the
   services need.
3. **Edit `common/production_config.yaml`** — replace the placeholder
   `allowed_origins` with the real domain.
4. **Real-world client test.** The smoke tests cover auth and the
   `initialize` handshake. Before announcing remote support, point an
   actual client (Cursor, Claude.ai) at the deployed `/mcp` and run
   `tools/list` + a `tools/call`.
5. **Subscribe to PR activity** if you want autofix on review comments.
   The CLI for this session has the `subscribe_pr_activity` tool ready
   to go once the PR is open.

---

*This file is temporary — feel free to delete once the PR is merged. It's
a transcript of reasoning, not a doc that belongs in the repo long-term.*
