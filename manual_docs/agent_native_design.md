# Agent-native features: design

Design for six features that make this template's MCP server friendlier to the
agents that use it: agent feedback, docs search, per-connection tool flags,
human-in-the-loop (HITL) approval modes, MCP events, and MCP skills. Each one
lands as its own PR, in this order:

```
main --+-- PR1 feedback (+ this doc)
       +-- PR2 search_docs
       +-- PR3 skills extension
       +-- PR4 risk tier + URL flags + tool annotations
              +-- PR5 approval modes + PendingAction + dashboard pages
                     +-- PR6 events outbox + SEP-3415 (also needs PR1)
```

## Architecture

```
                          +-------------------- Web dashboard (human session, WorkOS) ----+
                          |  /dashboard/approval   global mode + per-tool overrides       |
                          |  /dashboard/approvals  inbox: approve / deny                  |
                          |  (deeplinks.* URL templates can point at your own dashboard)  |
                          +------------------------------+--------------------------------+
                                                         | REST: GET/PUT /settings/approval
                                                         |       GET /approvals
                                                         |       POST /approvals/{id}/{approve|deny}
 Agent --> /mcp?approval=agent&dangerous_tools=false     v
           (query stripped before the RFC 8707 audience check)
 +------------------------------------------------------------------------------------+
 | MCP server                                                                         |
 |  tools/list: hide risk=dangerous if dangerous_tools=false (filter only)            |
 |  tools/call(dangerous): mode = max(dashboard floor/override, ?approval=)           |
 |     automatic -> run | else PendingAction -> agent: confirm_action(id)             |
 |                                            elicitation: in-host confirm (else ->   |
 |                                                         browser)                   |
 |                                            browser: link; server runs on approve;  |
 |                                                     get_approval(id) to poll       |
 |  extensions: io.modelcontextprotocol/skills  (skill://<name>/..., from skills/)    |
 |              io.modelcontextprotocol/events  (events/list, poll, subscribe/webhook)|
 +------------------------------------------------------------------------------------+
 | services (@service, risk='read'|'write'|'dangerous'; dangerous implies mutating)   |
 |  feedback          -> agent_feedback table -> FeedbackSink[] (Slack built in)      |
 |  search_docs       -> BM25 over a build-time bundled docs index (CI drift check)   |
 |  confirm_action, get_approval                                                      |
 +------------------------------------------------------------------------------------+
 | event outbox (shared) --> existing webhooks (sha256= signing, unchanged)           |
 |                       \-> SEP-3415 webhooks (Standard Webhooks, whsec_, verify, TTL)|
 | event types: approval.resolved, gmail.message_received, feedback.received          |
 +------------------------------------------------------------------------------------+
```

## PR1: feedback

Agents that get stuck report it to the operator instead of giving up silently.

- `@service("feedback", mutating=True)` with input
  `{message, category: bug|confusing_docs|missing_tool|other, tool_name?, context?}`.
  `user_id` is injected from the token like every other service; feedback uses
  the same auth as other tools.
- Every submission is stored in `agent_feedback` first, so nothing is lost when
  a sink is unset or down.
- Sinks implement `FeedbackSink` (`name`, `send(record)`) and are registered
  with `register_feedback_sink()`. A failing sink is logged and skipped; it never
  fails the agent's call. Slack (incoming webhook, `FEEDBACK_SLACK_WEBHOOK_URL`)
  is the one built-in. Linear, Jira and the like are left for template users.
- The message is agent-written, so Slack control characters are escaped
  (`<!channel>` cannot ping a channel).
- `mutating=True` means the REST route requires an `Idempotency-Key`, so a
  retried POST does not file duplicate reports.

## PR2: search_docs

- A `@service` (all three transports) that returns ranked chunks
  `{title, url, snippet}` from the existing BM25 retriever. It uses no LLM: the
  calling agent does the synthesis. `/ask` stays as it is.
- The index is generated at build time and shipped in the wheel (like the
  committed MCP App HTML), so deployments that do not copy `docs/` still work. A
  CI check fails when the bundled index drifts from `docs/content`.

## PR3: skills over MCP

- Implements `io.modelcontextprotocol/skills` (SEP-2640): `skills/list`,
  `skills/get`, and each file as a `skill://<name>/<path>` resource with the
  digest and size the spec requires. `directoryRead` stays off.
- Source is the hand-written `skills/<name>/` directory, bundled in the wheel.
- A CI check fails when a skill names a tool that is not in the registry.

## PR4: risk tier and URL flags

- `@service(risk="read"|"write"|"dangerous")`. `dangerous` implies `mutating`;
  `mutating` keeps its idempotency meaning. Creating a draft is `write`;
  sending it is `dangerous`.
- `risk` also sets MCP tool annotations: `dangerous` sets `destructiveHint`,
  `read` sets `readOnlyHint`.
- Flags travel as query parameters on the MCP URL, for example
  `/mcp?dangerous_tools=false&approval=browser`. The query is stripped before the
  RFC 8707 audience comparison, and protected-resource metadata advertises the
  bare `/mcp` as the canonical resource.
- `dangerous_tools=false` removes `risk=dangerous` tools from `tools/list` and
  rejects calls to them. It is a convenience filter, not a security control:
  an agent can edit its own URL. Dangerous tools are included by default.

## PR5: approval modes

Modes, from loosest to strictest:

| Mode | Who approves | Good for |
|---|---|---|
| `automatic` (default) | nobody; the tool runs | in-the-loop use, trusted models |
| `agent` | the agent, via `confirm_action(id)` | in-the-loop use, a second look |
| `elicitation` | the human, in the host's UI | the host relays the answer, the agent cannot |
| `browser` | the human, on a dashboard page | background agents; no token can self-approve |

- **Precedence:** the dashboard setting is the floor (a global mode plus
  optional per-tool overrides). `?approval=` on the URL can only tighten it.
  Effective mode = `max(dashboard, url)`, so an agent that edits its URL to
  `approval=automatic` gains nothing.
- **PendingAction:** every non-automatic call stores
  `{id, tool, args, args_hash, user_id, mode, expires_at}`. Each one is single
  use and bound to the user who made the call. Expiry is configurable per mode
  (defaults: 10 min for agent and elicitation, 24 h for browser).
- **Agent:** the tool returns a confirmation-required result with an id; the
  agent calls `confirm_action(id)`, which runs the stored call. Arguments cannot
  be swapped between the first call and the confirm.
- **Elicitation:** the host asks the human to confirm. If the client cannot
  elicit, the call escalates to browser, never to a looser mode.
- **Browser:** the tool returns a deep link to the approval page. Approving runs
  the stored call server-side; the agent reads the outcome with
  `get_approval(id)` (or, after PR6, an `approval.resolved` event).
- **Dashboard:** the template ships the REST API plus two bare server-rendered
  pages. They require a human browser session (WorkOS sign-in, reusing the
  built-in OAuth server's browser flow), never an agent bearer token. With
  WorkOS unset, a dev-only bypass refuses to run when `DEV_ENV=prod`.
  `deeplinks.*` URL templates in config can point at your own dashboard instead.
- With `dangerous_tools=false`, `confirm_action` and `get_approval` are hidden
  too.

## PR6: events

- Implements `io.modelcontextprotocol/events` (SEP-3415) with the webhook and
  poll delivery modes. Push (`events/stream`) is skipped: no client supports it
  yet, and long-lived streams fight the stateless, horizontally scaled
  deployment.
- One internal event outbox with two front-ends. The existing webhooks keep
  their `sha256=` format (no breaking change). SEP-3415 subscriptions get
  Standard Webhooks signing, client-supplied `whsec_` secrets, endpoint
  verification and `refreshBefore` TTLs.
- First-party event types: `approval.resolved`, `gmail.message_received`,
  `feedback.received`.

## Spec status (checked 2026-11-24)

- Skills (SEP-2640): marked Final; the client matrix shows partial support in
  ChatGPT and MCP Inspector.
- Events (SEP-3415): the SEP page says Final, the extensions overview says
  beta. Only ChatGPT is listed, with webhook mode only.

Re-check both before implementing PR3 and PR6; MCP moves quickly.
