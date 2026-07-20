# PRD: Code Spaghetti Remediation Plan

## Introduction

This document is the output of a full-codebase spaghetti audit (July 2026,
~35k lines of Python). Four parallel deep-reads covered `services/` + `models/`,
`api_server/`, `mcp_server/` + `src/`, and `common/` + `db/` + `utils/llm/` +
`init/` + `scripts/` + `tests/`. Every finding below was verified against the
code with exact file:line references; nothing is speculative.

The headline: **module-level layering is largely healthy** (zero
`services/` -> transport imports, zero CLI -> server imports), but the rot
lives one level down: a real `api_server <-> mcp_server` circular dependency,
business logic stranded in route files, "private" underscore helpers acting as
load-bearing cross-module APIs, import-time side effects everywhere, and a
`mutating=True` idempotency contract that most mutating services ignore.

### Intended vs. actual dependency graph

```
          INTENDED                                ACTUAL (verified)

  +-----------------------+          +------------------------------------------+
  | src/cli  api  mcp_srv |          |  api_server/server.py ---------------+   |
  |    |      |      |    |          |     | (mounts /mcp, top-level import)|   |
  |    v      v      v    |          |     v                                |   |
  |      services/        |          |  mcp_server/server.py                |   |
  |         |             |          |     |                                |   |
  |         v             |          |     v  (lazy, self-described        |   |
  |  models/ common/ db/  |          |  mcp_server/_tool_factory.py --------+   |
  +-----------------------+          |   "circular import" back-edges:         |
                                     |   -> api_server.auth.scopes              |
                                     |   -> api_server.billing.limits           |
                                     |                                          |
                                     |  routes/payments/*  <- Stripe state      |
                                     |     machine that never reaches services/ |
                                     |                                          |
                                     |  services/gmail_*  <-> sideways imports  |
                                     |     of _private helpers between peers    |
                                     |                                          |
                                     |  src/utils/output.py -> src/cli/state    |
                                     |     (utils importing a transport, up)    |
                                     +------------------------------------------+
```

### How to read this document

Each numbered item is a self-contained mini-PRD: problem, evidence,
implementation plan, acceptance criteria, and effort (S = under half a day,
M = 1-2 days, L = 3-5 days). Items are grouped by theme and ranked by priority
within each theme. The final section gives a suggested sequencing, because a
few items unlock many others.

### Priority index

| # | Item | Severity | Effort |
|---|------|----------|--------|
| 1 | Transport-neutral authz (break the api/mcp cycle) | HIGH | M |
| 2 | Stop building the MCP server at import time | HIGH | M |
| 3 | Enhancer fallback can run mutating services twice | HIGH | S |
| 4 | `mutating=True` contract applied to a minority of mutating services | HIGH | M |
| 5 | Stripe subscription state machine lives in a route file | HIGH | L |
| 6 | One credential resolver instead of four | HIGH | M |
| 7 | Config: import-time singleton with process-global side effects | HIGH | L |
| 8 | Test config isolation is illusory | HIGH | M |
| 9 | `services/gmail` package restructure (god module + sideways imports) | HIGH | L |
| 10 | Gmail test god-file split + `_patch_db` dedup | HIGH | M |
| 11 | `init/onboard.py` god module split | HIGH | M |
| 12 | Stripe gateway consolidation (checkout saga, dual status mappers) | MED | M |
| 13 | Unify the two idempotency/dedup mechanisms | MED | M |
| 14 | `rate_limit.py` package split | MED | M |
| 15 | Gmail plumbing dedup + the `_resolve_inline_images` name collision | MED | M |
| 16 | MCP-host prose baked into transport-agnostic service metadata | MED | M |
| 17 | Google OAuth lifecycle split across layers | MED | S |
| 18 | Gmail client cache: hidden module-global state | MED | S |
| 19 | `src/utils` is not a leaf; payments naming split | MED | M |
| 20 | `dspy_langfuse.py`: untestable 200-line callback, fake token counts | MED | M |
| 21 | Error envelope by response re-parsing | LOW-MED | M |
| 22 | Import-time side-effect sweep (routes, flags, logging, CLI) | MED | M |
| 23 | FastMCP private-internals shims scattered across modules | MED | S |
| 24 | Contract models outside `models/`; attachment shape sprawl | LOW | S |
| 25 | Scripts tree-walk boilerplate drift | MED | S |
| 26 | Undeclared env-var behavior switches | LOW | S |
| 27 | Small CLI hygiene (state naming, silent enhancer misregistration) | LOW | S |

---

## Theme A: Layering and transport boundaries

### Item 1: Extract transport-neutral authz; break the `api_server <-> mcp_server` cycle

**Severity: HIGH. Effort: M.**

**Problem.** The architecture rule is "transports never import each other,"
with one sanctioned seam (`api_server/server.py` mounts `/mcp`). In reality
every MCP tool call reaches back into the HTTP layer, and the cycle is
acknowledged in comments rather than fixed.

**Evidence.**
- `mcp_server/_tool_factory.py:40-64`: call-time imports of
  `api_server.auth.scopes.check_scopes` and
  `api_server.billing.limits.ensure_daily_limit`, with the comment
  "Circular import: api_server.server mounts mcp_server.server, which imports
  this module - api_server.* must only load at call time."
- `mcp_server/app_tools/_auth_guard.py:28` imports the underscore-private
  `_check_quota, _check_scopes` from `_tool_factory`, so app tools depend on
  the factory's internals too.
- `api_server/billing/limits.py:143,214`: `ensure_daily_limit` raises
  `fastapi.HTTPException(status_code=402, ...)` and is invoked from inside MCP
  tool calls, where an HTTP status code and `Retry-After` header are
  meaningless to the JSON-RPC transport.
- `api_server/routes/well_known.py:110` completes a second lazy back-and-forth
  with `from mcp_server.server import llm_tool_surface`.
- `src/utils/current_user.py:12,14`: the per-request auth contextvar that both
  transports share lives in a tree documented as "CLI plumbing + shared
  utils."

**Why it is spaghetti.** Scope checking and daily quota are cross-transport
policy, not HTTP-server code. Because they live in `api_server`, the MCP layer
must lazily import upward, private helpers leak across packages, and a
transport-specific exception type crosses into a different transport.

**Implementation plan.**
1. Create a transport-neutral home: `common/authz.py` (scope checking, the
   `current_user` contextvar moved from `src/utils/current_user.py`) and
   `services/quota_svc.py` (daily-limit logic, currently in
   `api_server/billing/limits.py`).
2. Define domain exceptions there: `InsufficientScopeError`,
   `QuotaExceededError` (carrying limit/remaining/reset metadata, no HTTP
   concepts).
3. Map per transport: `api_server` exception handlers translate to 403/402
   JSON with `Retry-After`; `mcp_server/_tool_factory.py` translates to a tool
   error. Both transports now import downward only.
4. Promote `_check_quota`/`_check_scopes` (or their replacements) to public
   names consumed by both `_tool_factory.py` and `app_tools/_auth_guard.py`.
5. Delete every lazy `api_server.*` import inside `mcp_server/` and the
   "circular import" comments. Add an `import-linter` contract forbidding
   `mcp_server -> api_server` imports so the cycle cannot return.

**Acceptance criteria.**
- `grep -rn "api_server" mcp_server/` returns nothing.
- `make import_lint` enforces the new contract.
- Quota-exceeded behavior over REST (402 + `Retry-After`) and over MCP (tool
  error with retry guidance) is covered by tests.

---

### Item 2: Stop building the MCP server as an import side effect

**Severity: HIGH. Effort: M.**

**Problem.** Importing `mcp_server` anywhere triggers full service discovery,
pkgutil-imports every enhancer and app tool, reads config, and registers all
tools on a module-level singleton.

**Evidence.**
- `mcp_server/server.py:242`: `build_mcp_server()` executed at module import,
  with the comment "Populate the singleton at import time so tests ... see
  registered tools without an explicit build call."
- `mcp_server/__init__.py:3` imports `server`, so `import mcp_server` alone
  fires the whole build.
- `app_tools/*` import the singleton back (`from mcp_server.server import
  mcp`), a deliberate import cycle that only works because of statement
  ordering inside `build_mcp_server` (deferred imports at
  `mcp_server/server.py:139-140` carry their own explanatory comments).
- Registration is by-import throughout: nothing that defines a tool is ever
  called by name from anywhere, making the flow maximally hard to trace.

**Why it is spaghetti.** Import order becomes load-bearing, test isolation
requires the `_populated` guard global, and any module that merely wants a
type from `mcp_server` pays for (and depends on) a full server build.

**Implementation plan.**
1. Delete the import-time `build_mcp_server()` call. The three real
   entrypoints (`mount_on`, `lifespan`, stdio `main`) call it explicitly; add
   a pytest fixture that does the same for tests.
2. Break the `app_tools <-> server` cycle: each app-tool module exposes
   `register(mcp: FastMCP) -> None`; `discover_app_tools()` imports modules
   and calls `register` instead of modules importing the singleton back.
3. Do the same for enhancers if practical, or at minimum keep discovery inside
   `build_mcp_server` so it never runs at import.
4. Empty `mcp_server/__init__.py` of the `server` import (export lazily via
   `__getattr__` if needed for compat).

**Acceptance criteria.**
- `python -c "import mcp_server"` performs no service discovery, no config
  read, no tool registration (assert via a regression test that counts
  registered tools before/after an explicit build).
- No module in `mcp_server/app_tools/` imports `mcp_server.server`.

---

### Item 16: Remove MCP-host prose from transport-agnostic service metadata

**Severity: MED. Effort: M.**

**Problem.** Service `description` strings and registry exceptions are written
as prompts for an MCP host, but the same strings become the REST OpenAPI
summaries and CLI help.

**Evidence.**
- `services/gmail_drafts_svc.py:170-187`: description says "open an
  interactive composer UI... keep your chat response to one brief sentence
  since the user can edit in the UI"; similar at `:254-267`, `:379`.
- `services/gmail_messages_svc.py:312,342,425`: "on vision-capable MCP hosts,
  image attachments are additionally rendered into context."
- `services/gmail_curate_svc.py:262`: "render the inbox dashboard... When an
  interactive UI is rendered alongside the result..."
- `services/__init__.py:20-34`: the base `ConnectRequiredError` hard-codes an
  MCP concept ("the MCP layer converts this into a SEP-1036 URL elicitation")
  and carries `elicitation_message`.
- Consumed by non-MCP transports at `api_server/routes/services.py:35`
  (`summary=entry.description` -> OpenAPI) and
  `api_server/routes/well_known.py:112`. A curl user is told to keep their
  chat response brief.

**Implementation plan.**
1. Add an optional `mcp_description=` field to `ServiceEntry`
   (`services/__init__.py`); keep `description` neutral and one-line.
2. `mcp_server/_tool_factory.py` prefers `mcp_description` when present;
   REST/CLI keep using `description`.
3. Move the host-prompt prose for the gmail family into `mcp_description=` (or
   an overlay map in `mcp_server/` if keeping services fully prose-free is
   preferred).
4. Rename `elicitation_message` to a transport-neutral `user_prompt` on
   `ConnectRequiredError`; the MCP layer decides it maps to elicitation.

**Acceptance criteria.**
- `grep -rniE "MCP host|chat response|interactive.*UI|elicitation" services/`
  returns nothing.
- OpenAPI summaries read as API documentation; MCP tool descriptions keep
  their host guidance (assert both in the e2e wire-format test).

---

### Item 17: Reunify the Google OAuth lifecycle behind one service function

**Severity: MED. Effort: S.**

**Problem.** The OAuth flow is split down the middle between a route and a
service, glued by a private cross-package import.

**Evidence.**
- `api_server/routes/google_oauth.py:29-33`: `from services.gmail_svc import
  GOOGLE_TOKEN_ENDPOINT, _verify_state, gmail_connect` (an underscore-private
  symbol crossing a package boundary).
- The authorization-code exchange (`_exchange_code`,
  `google_oauth.py:111-138`) and the `GoogleToken` upsert (`_upsert_token_row`,
  `:141-171`) live in the route, while the refresh-token exchange against the
  same `GOOGLE_TOKEN_ENDPOINT` (`_mint_access_token`, `gmail_svc.py:351`)
  lives in the service.
- The route also starts the Gmail watch itself (`_maybe_start_watch`,
  `:240-257`) via more lazy imports.

**Implementation plan.**
1. Add `gmail_oauth_complete(code, state)` to the gmail service layer, owning
   state verification, code exchange, token persistence, and watch start.
2. Reduce the callback route to: call the service, render success/error HTML.
3. Stop exporting `_verify_state`; it becomes internal to the service.

**Acceptance criteria.**
- `google_oauth.py` contains no token-endpoint HTTP calls and no DB writes.
- No `_`-prefixed name is imported across a package boundary in this flow.

---

### Item 19: Make `src/utils` a leaf again; give payments one name per domain

**Severity: MED. Effort: M.**

**Problem.** Two inversions in the utils tree, plus "payments" naming two
unrelated systems in three places.

**Evidence.**
- `src/utils/output.py:8` and `src/utils/progress.py:9` import
  `src.cli.state` - shared utils importing a transport, upward. Any non-CLI
  consumer of `render()` drags in CLI contextvars.
- `src/utils/current_user.py` is the load-bearing auth seam between
  `api_server/middleware/mcp_auth.py:29` and `mcp_server/_tool_factory.py:28`,
  yet lives in "CLI plumbing + shared utils" (moves in Item 1).
- `src/payments/` is the agentic x402 protocol stack with exactly one
  consumer (`api_server/routes/agentic_payments.py:45,71,124`), while Stripe
  billing lives in `api_server/billing/` + `api_server/routes/payments/`.
  Grep for "payments" returns both domains.
- `PaymentRegistry` is a hand-rolled double-checked-locking singleton whose
  `initialize()` is called per-request in three route handlers instead of once
  in the app lifespan.
- Two parallel utils trees (`src/utils/` and top-level `utils/llm/`) with no
  documented rule for which one anything belongs in.

**Implementation plan.**
1. Move the `output_format`/`verbosity` contextvars from `src/cli/state.py`
   into `src/utils/` (the CLI sets them; utils own them). Imports then point
   downward only.
2. Move `src/payments/` to `api_server/agentic_payments/` (single consumer)
   or a top-level `payments/` infra package; pick one and update
   `import_lint` contracts.
3. Call `PaymentRegistry.get().initialize()` once in the api_server lifespan;
   delete the three per-request calls.
4. Document (or collapse) the `src/utils` vs `utils/llm` split in CLAUDE.md.

**Acceptance criteria.**
- `grep -rn "src.cli" src/utils/` returns nothing.
- "payments" grep resolves to one domain per package name; docs updated.
- Registry initialization happens exactly once per process (assert with a
  counter in tests).

---

### Item 22: Import-time side-effect sweep

**Severity: MED. Effort: M.**

**Problem.** Beyond Items 2 and 7, several modules do real work (config reads,
network-capable client construction, registry scans) at import.

**Evidence.**
- `api_server/routes/ask.py:25,34`: imports the private
  `_build_storage, _client_ip` from the rate-limit middleware, then builds a
  second rate limiter at import time
  (`_limiter = MovingWindowRateLimiter(_build_storage())`) - a config read and
  potential Redis connection on import.
- `api_server/routes/services.py:63`: `_register_service_routes()` executes at
  module bottom, running full service discovery as an import side effect.
- `common/flags.py:27`: `setup_feature_flags()` at import chains OpenFeature
  provider registration onto every `from common.flags import client`.
- `src/utils/logging_config.py:11,72`: `_SCRUBBER = _LogScrubber()` at import
  loads full `global_config` and constructs a `scrubadub.Scrubber` even if
  `setup_logging()` is never called.
- `src/cli/commands/secrets.py:26-33`: `_SERVICE_NAME = _get_cli_name()` at
  module scope runs an `importlib.metadata.entry_points()` scan on every CLI
  invocation (commands are eagerly discovered), contradicting the folder's own
  rule to keep `--help` fast; `keyring`/`dotenv` imported at top.

**Implementation plan.**
1. `ask.py`: make the limiter a `functools.cache`d accessor; consume the
   public storage builder from Item 14's `rate_limit/storage.py`.
2. `routes/services.py`: convert to an explicit `build_router()` called from
   `server.py`.
3. `flags.py`: initialize on first `client` use (module `__getattr__` or a
   cached accessor).
4. `logging_config.py`: construct the scrubber lazily on first
   `setup_logging()`/`scrub_sensitive_data` call.
5. `secrets.py`: `functools.cache` the service-name lookup at call time; move
   `keyring` imports inside handlers.

**Acceptance criteria.**
- Importing any `api_server.routes.*` module performs no storage/registry
  construction (regression test with an import-in-subprocess probe).
- `mymcp --help` does not touch `importlib.metadata` entry-point scanning for
  secrets (measurable startup-time win).

---

## Theme B: Billing, payments, and the mutation contract

### Item 5: Move the Stripe subscription state machine out of the webhooks route

**Severity: HIGH. Effort: L.**

**Problem.** The entire subscription lifecycle is implemented inside a
585-line route file, with zero involvement of `services/`, plus module-level
mutable process state wired into the request path.

**Evidence.** All in `api_server/routes/payments/webhooks.py`:
- Tier resolution `_resolve_tier` (:236), Stripe-to-local status mapping
  `_map_stripe_status` (:220), out-of-order event protection `_is_stale_event`
  (:281), event dedup `_mark_event_processed` (:204), quota resets, and five
  DB-writing handlers (:298-585).
- Module-level mutable state for background cleanup:
  `_last_cleanup = time.monotonic()`, `_cleanup_lock = threading.Lock()`
  (:139-142), and a probabilistic sweep inside the request handler
  (`if random.random() < 0.01 or _cleanup_overdue()`, :132).
- The five handlers copy-paste an identical ~25-line skeleton (dedup check ->
  customer lookup -> `_CustomerNotFoundError` -> stale check -> commit); e.g.
  :303-323 vs :353-373 vs :421-443 are near-verbatim clones.
- The file is exempted from the 500-line CI limit (`pyproject.toml`,
  `[tool.file_length]`).

**Implementation plan.**
1. Create `api_server/billing/subscription_sync.py` (or `services/
   billing_svc.py` if it should be transport-visible) owning: status mapping,
   tier resolution, staleness, dedup, and the five event handlers.
2. Factor the repeated skeleton into one
   `_with_deduped_subscription(event, handler)` wrapper; each event type
   becomes ~15 lines of delta logic.
3. Move the periodic cleanup sweep into `api_server/runner.py`, which already
   runs `cleanup_delivered` on the same cadence (`runner.py:30-34`). Delete
   the module-level lock/timer/`random.random()` from the request path.
4. The route keeps only: signature verification, event parsing, dispatch.
5. Remove the file's exemption from `[tool.file_length]`.

**Acceptance criteria.**
- `webhooks.py` under 150 lines; no module-level mutable state; exemption
  removed.
- Unit tests for the state machine run without FastAPI or a test client.
- Existing webhook integration tests still pass unchanged.

---

### Item 12: One Stripe gateway; dissolve the checkout saga helpers

**Severity: MED. Effort: M.**

**Problem.** Stripe SDK operations are smeared across `billing/` (config,
quota) and `routes/payments/*` (all actual operations), with the same
boilerplate and even the same status-mapping logic duplicated.

**Evidence.**
- `import stripe  # noqa: PLC0415` with the same justification comment appears
  8 times across 5 files (`checkout.py:37,109,203,254`, `webhooks.py:62,475`,
  `metering.py:52`, `subscription.py:89`).
- `reset_stripe_on_auth_error()` invoked from 4 call sites (`checkout.py:272`,
  `metering.py:66`, `subscription.py:111`, `error_handler.py:197`).
- Stripe-to-local status mapping exists twice: `webhooks._map_stripe_status`
  (:220-233) vs `subscription._get_stripe_status` (:93-98), both re-deriving
  `CANCELING` from `cancel_at_period_end`.
- ~170 of `checkout.py`'s 290 lines are a concurrency saga
  (`_ensure_stripe_customer` with `with_for_update` row locking,
  `_recover_concurrent_customer`, `_delete_orphaned_customer`), raising
  `HTTPException` from deep helper code; a dead branch `if sub is None:`
  nested inside `if sub:` at :151.
- The hand-rolled TTL cache in `subscription.py:37-52` duplicates the one in
  `rate_limit.py` (see Item 14).

**Implementation plan.**
1. Create `api_server/billing/stripe_gateway.py` owning every Stripe SDK
   call: one lazy import, one auth-error-reset wrapper, one status mapper, the
   customer-ensure/recover/delete saga.
2. Routes become parse -> gateway/service -> serialize; saga helpers raise
   domain errors, mapped to HTTP at the route/handler boundary.
3. Delete the dead `if sub is None:` branch.
4. Move Stripe error-type sniffing out of `error_handler.py` (see Item 21)
   into the gateway.

**Acceptance criteria.**
- `import stripe` appears in exactly one module.
- One status mapper, unit-tested for the `CANCELING` derivation.
- Saga logic unit-testable without FastAPI.

---

### Item 13: One idempotency/dedup mechanism

**Severity: MED. Effort: M.**

**Problem.** The template ships a well-built generic `Idempotency-Key` engine,
but metering re-implements the contract by hand on a different table, and its
data hygiene silently depends on another route's traffic.

**Evidence.**
- Generic engine: `api_server/idempotency.py` (`execute_idempotent`,
  claim/replay/409/422 semantics, `idempotency_keys` table).
- `routes/payments/metering.py:93-176` re-validates keys by hand (:93-97
  duplicates `idempotency.py:49-61` including the 422 wording) and abuses
  `ProcessedStripeEvent` as a dedup store with a `meter:` key prefix (:132).
- Retention is enforced by the webhook module's cleanup:
  `webhooks._cleanup_old_events` docstring admits "webhook dedup records and
  `meter:` metering idempotency keys share the same 7-day retention window"
  (:157-163). If webhook traffic stops, metering keys stop expiring.

**Implementation plan.**
1. Route metering through `execute_idempotent` (its claim/replay model handles
   the Stripe-then-commit ordering), or extract the dedup-key primitive into
   `idempotency.py` and have both callers use it.
2. Give `ProcessedStripeEvent` cleanup a single owner in `runner.py`
   (composes with Item 5 step 3).

**Acceptance criteria.**
- No key-validation logic outside `idempotency.py`.
- Retention of metering dedup keys does not depend on webhook traffic
  (unit test advancing time with zero webhook events).

---

### Item 4: Enforce the `mutating=True` contract everywhere it applies

**Severity: HIGH. Effort: M.**

**Problem.** Per the registry contract (`services/__init__.py:60-64` and
CLAUDE.md), mutating services get REST `Idempotency-Key` enforcement. Only 4
services declare it; at least 12 others create/delete/modify state. The
idempotency machinery is dead code for most endpoints that need it.

**Evidence.**
- Declared: `gmail_update_draft`, `gmail_send`, `gmail_reply_to_thread`
  (`gmail_drafts_svc.py:190,296,382`), `inbox_save_curation`
  (`inbox_curation_svc.py:429`).
- Undeclared but mutating: `gmail_compose` creates a draft
  (`gmail_drafts_svc.py:253-270`); `gmail_discard_draft` deletes one
  (:308-313); `gmail_add_attachment`/`gmail_remove_attachment` do whole-message
  `drafts().update` (`gmail_attachments_svc.py:83-94,121-131`); the four
  thread services write Gmail labels and the curation ledger
  (`gmail_messages_svc.py:458-528`, writes at :489,508);
  `webhook_subscribe`/`unsubscribe`/`rotate_secret` create rows and mint or
  invalidate secrets (`webhooks_svc.py:164-260`); `gmail_watch_start/stop`
  mutate Pub/Sub state and the token row (`gmail_watch_svc.py:85-115`).
- Concrete failure: a retried `POST /services/gmail_compose` silently
  duplicates drafts.

**Implementation plan.**
1. Audit every `@service` against the "create/charge/send" rule; set
   `mutating=True` on the twelve-plus listed above.
2. Add a guard so this cannot regress: a lint/test that flags any service
   whose function calls `.execute()` on a non-`get`/`list` Gmail verb, or
   opens a DB write, without declaring `mutating` (a pragmatic AST or
   naming-convention check is fine; perfection is not required, drift
   detection is).
3. Verify the API contract change is acceptable: these endpoints will now
   require `Idempotency-Key`. Update docs and the OpenAPI examples.

**Acceptance criteria.**
- Retrying `POST /services/gmail_compose` with the same key replays instead of
  duplicating (integration test).
- The guard from step 2 runs in `make ci`.

---

### Item 3: Fix the enhancer fallback double-execution of mutating services

**Severity: HIGH. Effort: S.**

**Problem.** On any enhancer exception the tool factory retries headless,
re-running the service. Every shipped enhancer calls `tool.call()` first and
does UI work after, so a UI-stage crash re-executes the service. For mutating
services this means duplicate drafts or double charges.

**Evidence.**
- `mcp_server/_tool_factory.py:141-156`: `except Exception:  # noqa: BLE001`
  then `result = func(input_obj)` (fresh execution).
- `mcp_server/enhancers/gmail_composer.py:28-31`: `tool.call()` before
  `send_app()`; a crash in `send_app()` re-creates the draft.
- The registry knows better: `ServiceEntry.mutating`
  (`services/__init__.py:17,60`) exists precisely for create/charge/send, and
  REST gets idempotency protection while the MCP fallback ignores the flag.

**Implementation plan.**
1. Record on `EnhancedTool` whether `call()` completed and stash the result;
   the fallback path reuses that result instead of re-invoking `func`.
2. If no prior result exists and `entry.mutating` is true, propagate the error
   instead of silently re-executing. Decide this at registration time in
   `_make_enhanced_tool`, where `ServiceEntry.mutating` is already in hand.
3. Unit tests: enhancer raises after `call()` (result reused, service invoked
   once); enhancer raises before `call()` on a mutating service (error
   propagates); same on a non-mutating service (headless retry allowed).

**Acceptance criteria.** The three tests above pass; a counter-instrumented
fake service proves single execution in the crash-after-call path.

---

## Theme C: Auth and middleware

### Item 6: One credential resolver instead of four

**Severity: HIGH. Effort: M.**

**Problem.** The `Authorization: Bearer` + `X-API-KEY` parsing chain is
implemented four times with divergent failure semantics and divergent error
shapes.

**Evidence.**
- Implementations: `api_server/auth/unified_auth.py:22-61`;
  `api_server/middleware/mcp_auth.py:62-100` (AuthKit -> WorkOS -> API key);
  `api_server/middleware/rate_limit.py:131-283` (`_identity` and again
  `_resolve_tier`, which independently re-verify the WorkOS JWT and re-hash
  the API key, coordinating via ad-hoc `request.state._rl_user_id` /
  `_rl_user_id_resolved` flags at :153,:164,:252-260);
  `api_server/routes/ask.py:44-56`.
- Divergent semantics: `unified_auth` fails fast on a bad Bearer only
  `if global_config.WORKOS_CLIENT_ID` (:47-48); `mcp_auth` fails when either
  `WORKOS_CLIENT_ID or WORKOS_AUTHKIT_DOMAIN` is set (:86-87); `rate_limit`
  swallows every verification error and silently falls through
  (`except Exception: pass`, :157-161).
- Divergent rendering: REST 401 is `{"detail": ...}`; MCP 401 is a hand-built
  JSON-RPC `-32001` body (`mcp_auth.py:112-118`).

**Implementation plan.**
1. Extract `resolve_credentials(headers) -> AuthenticatedUser | None` in
   `api_server/auth/`, taking an ordered list of verifiers (authkit, workos,
   api_key) so the precedence rules are stated once.
2. The FastAPI dependency, the MCP middleware, and the rate limiter all call
   it; the rate limiter reuses the resolved principal via `request.state`
   (which it already half-does) and deletes its private JWT/key pipeline.
3. Keep per-transport 401 rendering separate; the decision is single-sourced.
4. Table-driven tests for the config-combination semantics (WORKOS_CLIENT_ID
   set/unset x AUTHKIT_DOMAIN set/unset x bad token) so today's divergence
   becomes one documented behavior.

**Acceptance criteria.**
- JWT verification and API-key hashing each appear in exactly one module.
- The config-combination test table passes against both REST and MCP paths.

---

### Item 14: Split `rate_limit.py` into a package

**Severity: MED. Effort: M.**

**Problem.** 565 lines mixing five concerns: config access, auth duplication,
a hand-rolled TTL cache, a Redis-retry state machine, and HTTP glue. Exempted
from the file-length limit.

**Evidence.** All in `api_server/middleware/rate_limit.py`:
- The defensive `try: from common import global_config ... except
  (ImportError, AttributeError)` block copy-pasted four times
  (`_build_storage` :48-54, `_get_tier_limits` :83-95, `_trust_proxy_headers`
  :102-111, `_should_skip` :391-404).
- Hand-rolled size-capped TTL cache (`_tier_cache`, :36-40,:224-238),
  duplicated nearly line-for-line in `routes/payments/subscription.py:37-52`.
- Redis-retry with double-double-checked locking (`_get_limiter`, :312-358).
- 429 building and `RateLimit-Policy` header formatting with the policy
  f-string duplicated at :440 and :561-563.
- Auth duplication covered in Item 6.

**Implementation plan.**
1. Split into `api_server/middleware/rate_limit/`: `storage.py` (Redis/memory
   bootstrap + retry, public names so `ask.py` stops importing privates),
   `identity.py` (delegates to Item 6's resolver), `policy.py` (reads
   `global_config.rate_limit` once, tolerates absence in one place),
   `middleware.py` (dispatch + headers, one policy-string formatter).
2. Extract a generic `TTLCache` into `src/utils/` and use it here and in
   `subscription.py`.
3. Remove the file's `[tool.file_length]` exemption.

**Acceptance criteria.**
- Each submodule under 200 lines; exemption removed; one config-access path;
  one TTL cache implementation in the repo.

---

### Item 21: Produce the error envelope at the source, not by response re-parsing

**Severity: LOW-MED. Effort: M.**

**Problem.** The consistent error envelope is achieved by buffering every
non-2xx JSON response, re-parsing its body, and heuristically rewriting
`{"detail": ...}` shapes, and the generic middleware imports a vendor SDK
concept.

**Evidence.**
- `api_server/middleware/error_handler.py:112-208`
  (`_rewrite_error_response`), including chunked draining of oversized bodies;
  routes raise three different `detail` shapes (string, list, dict-with-code,
  e.g. `billing/limits.py:145-153`) and the middleware guesses which is which.
- Vendor coupling: `from api_server.billing.stripe_config import
  reset_stripe_on_auth_error` (:12) and `_is_stripe_error` matching on
  `type(exc).__module__.startswith("stripe")` (:44-55).

**Implementation plan.**
1. Register `app.exception_handler(HTTPException)` and
   `RequestValidationError` handlers that emit the envelope directly (the
   `attachment_too_large_handler` at :211-219 already demonstrates the
   pattern).
2. Shrink the middleware to catch-all 500s + request-ID stamping.
3. Move Stripe error translation into the gateway from Item 12 so the
   top-level handler stays vendor-neutral.

**Acceptance criteria.** No response-body re-parsing in middleware; envelope
shape covered by tests for all three current `detail` shapes.

---

## Theme D: The Gmail service family

### Item 9: Restructure `services/gmail_*` into a package with a real internal API

**Severity: HIGH. Effort: L.**

**Problem.** `gmail_svc.py` (660 lines, exempted from the length limit) mixes
five concerns, and its underscore-"private" helpers are the de-facto shared
library for six sibling modules. Peer service modules also import each other's
privates sideways.

**Evidence.**
- Concerns in one file: domain errors (`gmail_svc.py:50-106`), OAuth constants
  + HMAC state signing (:117-185), DB token access (:193-222), client cache +
  token minting (:351-424), MIME build (:427-532), payload parsing (:535-660),
  plus the three registered connect/status/disconnect services (:230-343).
- Private imports by six siblings: `gmail_messages_svc.py:36-42`,
  `gmail_drafts_svc.py:52-59`, `gmail_draft_helpers.py:30`,
  `gmail_attachments_svc.py:27`, `gmail_curate_svc.py:48`,
  `gmail_watch_svc.py:42-47`, `inbox_curation_svc.py:56` (names like
  `_get_gmail_client`, `_parse_message_resource`, `_build_raw_message`,
  `_headers_to_dict`, `_get_db_session`, `_load_token_row`).
- Sideways peer imports: `gmail_curate_svc.py:47` pulls `_BATCH_CHUNK_SIZE,
  _internal_date_to_dt` from `gmail_messages_svc`; `inbox_curation_svc.py:48-56`
  reaches into the privates of three other service modules
  (`_batch_get_threads`, `_build_label_lookups`, `_score_thread`,
  `_thread_has_noise_labels`, `_find_mcp_done_label`, ...). The comment at
  `gmail_messages_svc.py:6-8` documents the tangle instead of fixing it.
- The graph is still a DAG (no cycle; `gmail_svc.py:329-331` uses a call-time
  import to keep it that way), but the direction is sideways, not downward:
  refactoring one registered-service file breaks unrelated services.
- Counterexamples that prove the target pattern works:
  `curation_ledger.py` and `webhook_delivery_svc.py` (single concern, public
  non-underscore API, clear transaction boundaries).

**Implementation plan.**
1. Create a `services/gmail/` package of non-registered helper modules:
   - `client.py`: token mint, client cache, `get_gmail_client`, token-row DB
     access (absorbs Item 18's factory).
   - `mime.py`: build/parse, b64 helpers, `headers_to_dict`, address helpers.
   - `errors.py`: the domain error hierarchy.
   - `labels.py`: `MCP_DONE_LABEL`, `EXCLUDE_LABELS`, query builder (Item 15
     step 4).
   - `batch.py`: the generic batch-get (Item 15 step 1).
2. Rename shared names to drop the leading underscore; they are the package's
   internal API, not module privates.
3. Service modules (`gmail_drafts_svc`, `gmail_messages_svc`, ...) import
   helpers and `models/` only, never each other. Enforce with an
   `import-linter` contract ("service modules are independent siblings").
4. `gmail_svc.py` shrinks to connect/status/disconnect + state signing (or is
   renamed `gmail_oauth_svc.py`); remove its length exemption and
   `gmail_messages_svc.py`'s once under 500.

**Acceptance criteria.**
- No `services/*_svc.py` imports another `*_svc.py`.
- No underscore-prefixed name imported across module boundaries in
  `services/`.
- Both gmail length exemptions removed from `pyproject.toml`.

---

### Item 15: Deduplicate Gmail plumbing; fix the `_resolve_inline_images` collision

**Severity: MED. Effort: M.** (Depends on Item 9's helper package.)

**Problem.** The same plumbing is written 2-4 times with drift, one duplicate
pair has divergent behavior that is a live bug, and two incompatible functions
share one name.

**Evidence.**
- Batch fetch written three times: `_batch_get_messages`
  (`gmail_messages_svc.py:225-257`) vs `_batch_get_threads`
  (`gmail_curate_svc.py:92-126`) - structurally identical chunk/batch/callback
  loops differing only in `.messages()` vs `.threads()` - plus a hand-rolled
  loop in `gmail_list_drafts` (`gmail_drafts_svc.py:120-141`).
- internalDate-to-datetime written three times
  (`gmail_messages_svc.py:75-81`, `gmail_svc.py:651-657`,
  `gmail_drafts_svc.py:84-89`), each with its own try/except.
- `_headers_to_dict` re-implemented inline at `gmail_watch_svc.py:208-211`
  even though the module already imports three other `gmail_svc` helpers.
- Thread-to-draft mapping exists twice with divergent behavior:
  `gmail_get_thread`'s inline scan (`gmail_messages_svc.py:359-371`, capped at
  `maxResults=50`, no pagination) vs `_build_draft_thread_map`
  (`gmail_curate_svc.py:201-227`, fully paginated, with a docstring explaining
  why non-pagination is a bug). The unpaginated copy has the exact
  false-negative the paginated one was written to fix (accounts with more
  than 50 drafts).
- Name collision: `_resolve_inline_images` in `gmail_messages_svc.py:180-219`
  (mutates `parsed` in place, embeds `data:` URIs for display) vs
  `gmail_draft_helpers.py:114-144` (returns `list[InlineImageUpload]` for MIME
  rebuild, mutates nothing). Same argument list; an import swap during a
  refactor would silently strip `cid:` images from saved drafts instead of
  erroring.
- Triage criteria stated three times: `_EXCLUDE_LABELS` set
  (`gmail_curate_svc.py:70-81`) vs the hand-typed `_CURATE_BASE_QUERY`
  re-listing every label as `-label:"..."` literals (:233-239) vs
  `_is_triageable` (`inbox_curation_svc.py:321-341`). The done-label name is
  duplicated across modules guarded only by a comment
  (`gmail_curate_svc.py:271-273`: "the exclusion must use that exact name").
  Drift does not error; done/noise threads silently reappear in triage.

**Implementation plan.**
1. One generic `batch_get(resource, ids, fmt, headers)` in
   `services/gmail/batch.py`, parameterized by resource; delete the three
   copies.
2. One `internal_date_to_dt` and one `headers_to_dict` in
   `services/gmail/mime.py`.
3. One paginated `draft_thread_map`; point `gmail_get_thread` at it. This is
   also a behavior fix; add a regression test with more than 50 drafts.
4. Derive the curate query from the data:
   `build_curate_query()` composed from `MCP_DONE_LABEL` and `EXCLUDE_LABELS`
   in `services/gmail/labels.py`; `_is_triageable` imports the same
   constants.
5. Rename the colliding functions to intent:
   `inline_cid_images_as_data_uris(parsed)` (display path, return a new dict
   instead of mutating) and `collect_inline_image_uploads(...)` (rebuild
   path).

**Acceptance criteria.**
- Exactly one implementation of each plumbing function in the repo.
- The over-50-drafts regression test passes.
- No two functions in `services/` share a name with incompatible contracts.

---

### Item 18: Replace the module-global Gmail client cache with an explicit factory

**Severity: MED. Effort: S.**

**Problem.** The "pure functions" layer holds process-global mutable state
keyed by user, with no eviction, no locking, and per-process-only
invalidation.

**Evidence.** `services/gmail_svc.py:379-380`
(`_client_cache: dict[str, tuple[float, Any]] = {}`), `:383-419`
(`_get_gmail_client`), `:422-424` (`_invalidate_gmail_client`, called only
from `gmail_disconnect` at :321).
- Entries are never evicted; memory grows with the user population.
- No lock; two threads for one user race on mint-and-build.
- After `gmail_disconnect`, a second worker/replica keeps serving the cached
  authorized client for up to `_CLIENT_TTL_S` (50 minutes): API calls succeed
  against an account the user believes is disconnected, until Google-side
  revocation catches up.

**Implementation plan.**
1. As part of Item 9's `services/gmail/client.py`: wrap the cache in a
   `GmailClientFactory` object (one instance, but an explicit, documented,
   testable seam).
2. Use `cachetools.TTLCache` (bounded, evicting) plus a lock around mint.
3. Cheaply check the token row's `revoked_at` before serving a cache hit, so
   cross-process disconnect takes effect immediately; document the residual
   reliance on Google-side revocation.

**Acceptance criteria.** Bounded cache with eviction test; disconnect-then-use
from a second "process" (fresh factory, same DB) fails closed.

---

### Item 24: Contract models back into `models/`; collapse attachment shapes

**Severity: LOW. Effort: S.**

**Problem.** Five registered wire-contract models live in a service module,
and `models/gmail.py` carries five overlapping attachment shapes plus a field
that exists only because of a committed frontend bundle.

**Evidence.**
- `services/gmail_messages_svc.py:45-67`: `GmailThreadModifyInput`,
  `GmailMarkReadResult`, `GmailArchiveResult`, `GmailMarkDoneResult`,
  `GmailUnmarkDoneResult` - registered `input_model`/`output_model` types
  (public schema over MCP and REST) outside `models/`, with the self-aware
  comment "Promote to models/gmail.py if reused elsewhere."
- `models/gmail.py:66-134,158-183,381-387`: `AttachmentInput`,
  `AttachmentUpload`, `InlineImageUpload`, `AttachmentReference`,
  `GmailDraftAttachment` vs `GmailAttachment` (same fields plus
  `content_id`/`data`), forcing mapping boilerplate
  (`gmail_drafts_svc.py:66-77`, `gmail_draft_helpers.py:147-162`).
- `models/gmail.py:173-183`: `size_bytes` computed field emitted alongside
  `size` "because the committed composer UI bundle still reads `size`" - the
  bottom layer shaped by a frontend artifact two layers up.

**Implementation plan.**
1. Move the five thread models into `models/gmail.py` (mechanical).
2. Make `GmailDraftAttachment` a subset/alias of `GmailAttachment` (one model,
   optional `content_id`/`data`).
3. Rebuild the composer app bundle to read `size_bytes`, then drop the dual
   emission; the computed-field workaround gets an expiry, not permanence.

**Acceptance criteria.** `models/` is the complete contract inventory (assert:
no `@service` registers a model defined outside `models/`); single attachment
read-shape; `size` dual-emission gone after the bundle rebuild.

---

## Theme E: Config, tests, and supporting infra

### Item 7: Config factory instead of import-time singleton

**Severity: HIGH. Effort: L.** (Unlocks Items 8 and 20's config half.)

**Problem.** Importing `common` mutates process env, reads mode flags, and
constructs (and can raise from) the full config object; `extra="allow"`
defeats the type checker; downstream modules freeze config values at import.

**Evidence.**
- `common/global_config.py:390-414`: `load_dotenv(...)` at import (:394),
  conditional `.prod.env` with `override=True` (:398), `DEV_ENV` /
  `GITHUB_ACTIONS` reads at import (:397,:401, plus field defaults at
  :280-286), `global_config = Config()` (:414) raising if YAML/.env is
  incomplete. 39 files import it; nothing is importable without valid config
  on disk.
- `common/config_models.py:177`: `SettingsConfigDict(... extra="allow")` - any
  misspelled YAML key silently becomes a live untyped attribute;
  `global_config.anything` type-checks as `Any`.
- `common/flags.py:27`: `setup_feature_flags()` at import (also in Item 22).
- `utils/llm/dspy_inference.py:32-35`: constructor defaults evaluated at
  class-definition time (`model_name: str =
  global_config.default_llm.default_model`); the Tenacity decorator (:75-81)
  bakes `stop_after_attempt(...)` / `wait_exponential(...)` into the function
  object at import. Config overrides applied after import are silently
  ignored.

**Implementation plan.**
1. Wrap construction: `@lru_cache def get_config() -> Config` owning
   `load_dotenv` and overlay logic; keep `global_config` as a thin lazy proxy
   (module `__getattr__`) so the 39 call sites migrate gradually.
2. Flip `extra="allow"` to `forbid` on `Config`; keep `allow` only on the one
   deliberate case (`FeaturesConfig`, `config_models.py:286`).
3. `dspy_inference.py`: defaults become `None`, resolved inside `__init__`;
   replace the decorator with a runtime-built `AsyncRetrying(...)` in
   `_run_with_retry` so retry policy reads current config per call.
4. Fix any misspelled-key fallout the `forbid` flip surfaces (it will;
   that is the point).

**Acceptance criteria.**
- `python -c "import common"` with no config files present does not raise.
- A misspelled key in `global_config.yaml` fails loudly at first
  `get_config()`.
- Changing retry config between two `DSPYInference.run` calls takes effect
  (test).

---

### Item 8: Real per-test config isolation

**Severity: HIGH. Effort: M.** (Depends on Item 7 for full resolution; the
fixture consolidation can land first.)

**Problem.** `TestTemplate` advertises per-test config isolation but isolates
nothing; tests mutate the live singleton through three different ad-hoc
idioms, where one forgotten restore poisons the rest of the suite.

**Evidence.**
- `tests/test_template.py:27-46`: deep-copies `global_config.to_dict()` and
  `setattr(self, key, value)` for ~40 untyped attributes per test class -
  invisible to type checkers and never read by production code (which reads
  the singleton).
- Idiom 1: `tests/test_ask_e2e.py:43-52` hand-rolls save/`finally`-restore of
  `global_config.ask.enabled`.
- Idiom 2: `tests/test_gmail_services.py:884,910`,
  `tests/test_api_server.py:148` use `patch.object(global_config.gmail, ...)`.
- Idiom 3: `tests/test_agentic_payments.py:104,126` saves/restores
  `global_config.payments` manually.

**Implementation plan.**
1. Delete the `setattr` loop from `TestTemplate` (keep `self.config` only if
   anything still reads it - almost nothing does).
2. Add one blessed `override_config(path, value)` fixture in
   `tests/conftest.py` built on `monkeypatch.setattr`; migrate the three
   idioms.
3. After Item 7: have the fixture produce a real per-test `Config` instance
   via the factory and clear the cache, making isolation actual rather than
   conventional.

**Acceptance criteria.** One override idiom repo-wide; a test that mutates
config and "forgets" to restore cannot affect the next test (demonstrated by
an ordering-sensitive regression pair).

---

### Item 10: Split the Gmail test god-file; stop copy-pasting `_patch_db`

**Severity: HIGH. Effort: M.**

**Problem.** One 2,588-line exempted test file holds 29 test classes spanning
five services plus another layer's privates, and an identical DB-patching
context manager is pasted into eight test files, all poking `db/engine.py`
privates.

**Evidence.**
- `tests/test_gmail_services.py` (exempted in `pyproject.toml`): 29
  `TestTemplate` subclasses covering `gmail_svc` helpers, drafts, messages,
  attachments, curate, MIME round-trips, and MCP omit-vs-null semantics
  (:1600); it imports privates across layers - `from
  mcp_server.app_tools.gmail_composer import _coerce_attachments,
  _patch_attachments` (:28-31). Fixture builders live at four scroll depths
  (:137, :2057, :2387, :2500).
- `_patch_db` duplicated in eight files (`test_gmail_services.py:90`,
  `test_gmail_watch.py:44`, `test_google_oauth.py:46`,
  `test_inbox_curation.py:76`, `test_mcp_e2e.py:44`, `test_mcp_remote.py:30`,
  `test_webhook_settings.py:29`, `test_webhooks.py:49`), one admitting "same
  pattern as tests/test_google_oauth.py"; each sets
  `db_engine._engine = eng; db_engine._SessionLocal = session_factory`.
- `_seed_token` duplicated (`test_gmail_services.py:109`,
  `test_gmail_watch.py:72`). `tests/conftest.py` is 6 lines; the natural home
  sits empty.

**Implementation plan.**
1. Add a sanctioned test seam to `db/engine.py` (e.g.
   `set_engine_for_testing(engine)` or an engine-override parameter) so tests
   stop reaching into `_`-prefixed globals.
2. Move one `_patch_db` (fixture + context manager) and `_seed_token` into
   `tests/conftest.py`; delete the eight copies.
3. Split the god file along the seams it already mirrors: `tests/gmail/`
   package with a `conftest.py` (payload builders, mock service factory) and
   `test_drafts_svc.py`, `test_messages_svc.py`, `test_attachments_svc.py`,
   `test_curate_svc.py`, `test_mime_roundtrip.py`; the composer app-tool tests
   move beside the enhancer tests. (`tests/cli/` and `tests/healthcheck/`
   prove the subpackage pattern exists.)
4. Remove both test exemptions (`test_gmail_services.py`,
   `test_inbox_curation.py` - split the latter the same way) from
   `pyproject.toml`.

**Acceptance criteria.** No test file over 500 lines; `db_engine._engine`
appears only inside `db/engine.py`; same test count before/after (no silent
drops - compare `pytest --collect-only -q | wc -l`).

---

### Item 11: Split `init/onboard.py` into a package

**Severity: HIGH. Effort: M.**

**Problem.** A 2,198-line permanently-exempted Typer module carrying at least
eleven distinct concerns that share almost nothing except `PROJECT_ROOT` and
`console`.

**Evidence.** `/init/onboard.py` (exempted in `pyproject.toml`): headless
onboarding config + pruning engine (:117-925), interactive orchestrator
(:926-1103), branding/emoji/color pickers (:1105-1230), a whole-repo
find-and-replace rename engine (:1231-1407), CLI renaming (:1408-1569), deps
(:1570), a `.env` wizard (:1608-1785), prek hooks (:1786), media generation
(:1861-1946), MCP enable/disable + distribution-file rewrites (:1947-2092),
GitHub workflow toggles (:2093-2198). The rename engine and the env wizard
have zero coupling to the pruning engine.

**Implementation plan.**
1. Convert to an `init/onboard/` package: `headless.py` (config + pruning),
   `orchestrator.py`, `branding.py`, `rename.py`, `envfile.py`, `mcp.py`,
   `workflows.py`, with a thin `app.py` composing the Typer app. Each
   `@app.command()` is already the module boundary; the repo does this exact
   auto-discovery pattern in `src/cli/commands/__init__.py`.
2. Move shared bits (`PROJECT_ROOT`, `console`, small helpers) into
   `init/onboard/_shared.py`.
3. Remove the exemption from `pyproject.toml`.

**Acceptance criteria.** No file in `init/` over 500 lines; exemption removed;
`uv run python -m init.onboard --help` output unchanged (snapshot test).

---

### Item 20: Make `dspy_langfuse.py` testable; stop reporting characters as tokens

**Severity: MED. Effort: M.**

**Problem.** One 200-line callback mixes exception mapping, three output-shape
parsers, cost math, and span lifecycle; when the provider returns no usage it
writes string lengths into token fields, corrupting observability data by
roughly 4x.

**Evidence.** `utils/llm/dspy_langfuse.py:162-368` (`on_lm_end`); the
fabricated usage at :276 (`final_prompt_tokens =
len(current_system_prompt + current_prompt)`) written into `usage_details`
(:301-306); proportional cost splitting at :307-323; a fresh `Langfuse()`
client per `DSPYInference` instance (:68).

**Implementation plan.**
1. Extract pure functions `_extract_completion(outputs) -> (content, model,
   usage, level, msg)` and `_compute_cost(usage, cost) -> cost_details`,
   unit-testable with no Langfuse; `on_lm_end` shrinks to ~30 lines of span
   plumbing.
2. Drop the char-count fallback (leave usage unset) or tag it
   `"estimated": True`; never write `len(str)` into a field named tokens.
3. Share one module-level Langfuse client via `get_client()`.

**Acceptance criteria.** Unit tests cover the three output shapes and the
no-usage case without a Langfuse client; no unestimated char-count usage.

---

### Item 23: Concentrate FastMCP private-internals access in one compat module

**Severity: MED. Effort: S.**

**Problem.** Three separate pokes into `mcp` SDK privates are scattered across
two modules with three different failure modes, so an SDK upgrade breaks three
places at once.

**Evidence.** `mcp_server/_tool_factory.py:188,203` (`_patch_output_schema`
reads `mcp._tool_manager._tools`, self-labelled "KNOWN FRAGILITY", and writes
`tool.__dict__["output_schema"]` to bypass Pydantic);
`mcp_server/server.py:224` (`sm._has_started = False` to allow session-manager
restart).

**Implementation plan.**
1. Create `mcp_server/_fastmcp_compat.py` holding all three shims, with a
   pinned-version assertion and one integration test per shim.
2. File upstream issues for public `output_schema` setting and session-manager
   re-entry; delete shims as they land.

**Acceptance criteria.** `grep -rn "_tool_manager\|_has_started" mcp_server/
--include="*.py" | grep -v _fastmcp_compat` returns nothing; an `mcp` version
bump has exactly one file to reconcile.

---

### Item 25: One tree-walk policy for the check scripts

**Severity: MED. Effort: S.**

**Problem.** Three CI checkers hand-roll `REPO_ROOT` + skip-dir sets, and they
have already drifted; a new vendored directory must be added in three or four
places or one checker silently scans `node_modules`.

**Evidence.** `scripts/check_file_length.py:20-34` (`SKIP_DIRS` with
`dist`/`build`); `scripts/check_blind_except_justification.py:21-37`
(uniquely contains `.venv-test`, plus a separate recursive set);
`scripts/check_ai_writing.py:8-19` (a third variant missing
`__pycache__`/`dist` at root, plus its own prefix and binary-suffix sets).
`scripts/sync_agent_config.py:306` already demonstrates the right approach:
`git ls-files` with a non-git fallback (:325-343).

**Implementation plan.**
1. Add `scripts/_repo_files.py` exposing `iter_source_files(suffixes=...)`
   backed by `git ls-files`, with the existing non-git walk as fallback.
2. Point the three checkers at it; delete their local skip sets.

**Acceptance criteria.** One skip policy; adding a vendored dir requires zero
checker edits; each checker's file census is unchanged (diff the file lists
before/after).

---

### Item 26: Declare the hidden env-var behavior switches

**Severity: LOW. Effort: S.**

**Problem.** Env discipline is mostly good (8 direct reads repo-wide), but two
reads are undiscoverable runtime toggles that belong in the `Config` model.

**Evidence.** `api_server/middleware/rate_limit.py:308`
(`self._testing = os.getenv("TESTING") == "1"` - a test-mode backdoor inside
production middleware, settable in prod, absent from config docs);
`mcp_server/enhancers/base.py:75` (`os.environ.get("MCP_DISABLE_APPS", "") ==
""` gating whole MCP-app behavior on an undeclared variable).

**Implementation plan.** Promote both to declared `Config` fields (the pattern
exists: `WEBHOOK_RUNNER_MODE` at `common/global_config.py:269`), read via
`global_config`, documented in the config YAML/README.

**Acceptance criteria.** `grep -rn "os.getenv\|os.environ" api_server/
mcp_server/ services/` shows no behavior toggles, only the documented
bootstrap exceptions.

---

### Item 27: Small hygiene - state-file naming, enhancer misregistration, stale docs

**Severity: LOW. Effort: S.**

**Problem and evidence.** Three small but trap-laden loose ends:
- `src/cli/state.py` (per-invocation contextvars) vs `src/cli/state_store.py`
  (persisted JSON) - near-identical names with no hint which is which; the
  store's API is raw `dict` in/out with magic string keys
  (`"telemetry_notice_shown"`, `"telemetry_enabled"`,
  `"security_notice_shown"`) hand-managed across three modules
  (`telemetry.py:41-49`, `security.py:154-166`) with read-modify-write races
  on one shared file; `telemetry.py:15` imports the private `_CONFIG_DIR`.
- Enhancer registration silently misses: nothing validates that an enhancer's
  `service_name` matches a real service - `@enhance("gmial_compose")`
  registers fine and the tool silently ships headless
  (`mcp_server/enhancers/__init__.py:36,54-62,73-82`).
- Stale machinery docs: CLAUDE.md still instructs adding `import
  mcp_server.enhancers.my_service  # noqa: F401` to `_register_tools()`, but
  `_register_tools` does not exist anywhere in the repo (grep hits only
  CLAUDE.md); actual registration is pkgutil auto-discovery.

**Implementation plan.**
1. Rename `state_store.py` to `persisted_state.py`; give it typed accessors
   (`get_flag(name)` / `set_flag(name)` or a small Pydantic model) so the key
   namespace lives in one place; export `config_dir()` publicly.
2. At the end of `build_mcp_server()`, assert
   `set(_enhancers) <= {e.name for e in get_registry()}` (same for
   `_EXCLUDED_DEFAULT_MCP_SERVICES`) and fail loudly on orphans.
3. Update CLAUDE.md's enhancer-registration instructions to describe the
   pkgutil auto-discovery that actually exists.

**Acceptance criteria.** A deliberately misspelled `@enhance("nope")` fails
the build; CLAUDE.md matches reality; state keys defined in exactly one
module.

---

## What is deliberately NOT on this list

For calibration, these were checked and found clean: `db/engine.py` (tidy
lazy-init), `db/base.py`, `common/token_encryption.py` (textbook Protocol +
two impls), `api_server/idempotency.py` internals, `api_server/pagination.py`,
`api_server/deprecation.py`, `api_server/runner.py`, `mcp_auth.py`'s ASGI
mechanics, `well_known.py`'s length (six distinct discovery documents -
breadth, not tangling), `src/payments` internal layering (properly acyclic),
CLI command thinness, and the `curation_ledger.py` / `webhook_delivery_svc.py`
service modules (the pattern the gmail family should converge on). The
`api_server/ask/` layering exception is documented and defensible; its core
has no FastAPI import and can move to `services/` for free if a second
transport ever wants it.

## Sequencing

```
  Wave 1 (foundations, unlock everything else)
  +----------------------------------------------------------+
  | Item 7  config factory        Item 10  test conftest/db  |
  | Item 8  test isolation        Item 3   enhancer dbl-exec |
  +----------------------------+-----------------------------+
                               |
  Wave 2 (boundaries)          v
  +----------------------------------------------------------+
  | Item 1  transport-neutral authz   Item 2  no import-time |
  | Item 6  credential resolver               MCP build      |
  | Item 4  mutating=True audit                              |
  +----------------------------+-----------------------------+
                               |
  Wave 3 (big splits)          v
  +----------------------------------------------------------+
  | Item 5  webhooks state machine    Item 9   gmail package |
  | Item 12 stripe gateway            Item 11  onboard split |
  | Item 14 rate_limit package                               |
  +----------------------------+-----------------------------+
                               |
  Wave 4 (dedupe + polish)     v
  +----------------------------------------------------------+
  | Items 13, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25,    |
  | 26, 27 - each independent, land in any order             |
  +----------------------------------------------------------+
```

Rationale: the config factory (7) and test seams (8, 10) make every later
refactor safely testable; the authz extraction (1) and import-time build
removal (2) must precede the big splits so the splits do not re-encode the
cycle; the dedupe items depend on the package homes created by the splits.
Item 3 is first-wave despite being small because it is a live
double-execution bug.

Every wave should leave `make ci` green, and every item that removes a
`[tool.file_length]` exemption must delete the corresponding line from
`pyproject.toml` in the same PR, so the guardrail ratchets instead of
accumulating grandfathered exceptions.
