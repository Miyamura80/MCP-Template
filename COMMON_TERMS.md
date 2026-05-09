# MCP Common Terms

Glossary of frequently confused **Model Context Protocol** terms. Consult this
before naming or designing new MCP-related code.

> ⚠️ **Spec baseline: 2025-11-25. Last reviewed: 2026-05-09.**
> MCP changes fast — recheck against the
> [MCP changelog](https://modelcontextprotocol.io/specification/2025-11-25/changelog)
> and the
> [2026 roadmap](https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/)
> if it has been **more than ~1 month** since this file was touched.
> Items flagged 🟡 below are known to be in-flight and most likely to drift.

## 1. Host vs. Client vs. Server

- **Host** — user-facing AI app that owns the LLM (Claude Desktop, Cursor).
- **Client** — 1:1 protocol connector *inside* the Host, one per Server.
- **Server** — external program exposing data/capabilities (this repo's `mcp_server/`).

One Host → many Clients → each talks to one Server. "MCP client" in blog
posts often means the Host — clarify which.

## 2. Tools vs. Resources vs. Prompts (server primitives)

| Primitive | Controlled by | Side effects | Use for           |
|-----------|---------------|--------------|-------------------|
| Tool      | Model         | Yes          | Function calls    |
| Resource  | Application   | No (read)    | Background context|
| Prompt    | User          | No           | Slash-command templates |
| **Tasks** 🟡 | Model     | Yes (async)  | Long-running ops (SEP-1686, experimental in 2025-11-25, expected to graduate) |

Decide by: does it mutate state → Tool; should the app inject it → Resource;
should the user pick it → Prompt; long-running / async → Tasks. Don't expose
read-only data as a Tool.

## 3. Sampling, Roots, Elicitation (client primitives)

Server-initiated requests *to* the Client (the split is fuzzier than it
sounds — these are server→client calls):

- **Sampling** — Server asks the Host's LLM to generate a completion.
- **Roots** — Client tells Server which URIs it's scoped to. Any valid URI
  (commonly `file://`, also `https://`); advisory boundary the server SHOULD
  respect, **not** an enforced permission. Auth must still be enforced separately.
- **Elicitation** — Server asks the user for structured input via JSON Schema.
  (Stable as of 2025-11-25; no longer "experimental".)

## 4. Resources vs. Roots

Both use URIs but differ:
- **Resource** = data the Server exposes (any scheme: `file://`, `db://`, custom).
- **Root** = scope boundary the Client declares (any URI; commonly `file://`).

Resources answer "what can I read?"; Roots answer "where am I scoped to?".

## 5. Transports

- **stdio** — local child process; simplest, fastest. Use for local tools.
- **Streamable HTTP** — single endpoint, request/response + SSE streaming,
  OAuth 2.1. Use for remote. The 2026 roadmap commits to no new official
  transports this cycle, so this is stable; a `.well-known` discovery layer
  is planned but additive.
- **SSE** — *deprecated*; legacy two-endpoint setup. Don't use for new servers.

## 6. JSON-RPC handshake

1. Client → `initialize` (protocolVersion, capabilities, clientInfo).
2. Server → response with its protocolVersion + capabilities.
3. Client → `notifications/initialized`.

**Capabilities** are negotiated feature flags — don't call methods whose
capability wasn't advertised. Mismatched `protocolVersion` terminates the
connection. Over Streamable HTTP, the version is also conveyed via the
`MCP-Protocol-Version` HTTP header.

## 7. OAuth 2.1 pitfalls

- MCP server is a **resource server**, not the authorization server (token factory).
- Sessions are anonymous by default. Identity comes from OAuth — note that
  machine identity is now first-class via SEP-1046 (`client_credentials`) and
  SEP-990 (Cross App Access / enterprise SSO).
- `resource` parameter must match the AS's expected URL **exactly** (trailing
  slash matters) — mismatches cause silent 401s. RFC 8707 was SHOULD in
  2025-06-18 and is moving to **mandatory** in the 2026-03-15 draft.
- Use **CIMD** (Client ID Metadata Documents, SEP-991) for client
  registration. Dynamic Client Registration (RFC 7591 / DCR) is now MAY, not
  the default.
- 🟡 **Scopes:** split per tool/capability and prefer least privilege. The
  2026-03-15 draft introduces **incremental scope consent** — the idiom is
  shifting from "ask for all scopes upfront" to "ask progressively as needed".
- 🟡 **Token storage:** persist tokens so users don't re-consent on each
  Host restart (a known account-takeover vector). This guidance changes once
  SEP-1932 (DPoP, sender-constrained tokens) and SEP-1933 (Workload Identity
  Federation) land — at that point you persist the *key*, not the bearer token.

## 8. Cheat-sheet

| If you hear…    | It means…                                              |
|-----------------|--------------------------------------------------------|
| "MCP client"    | Either the Host or its in-Host connector — clarify     |
| "MCP tool"      | Model-callable function; side effects allowed          |
| "MCP resource"  | Read-only data exposed by URI                          |
| "MCP prompt"    | User-triggered template (not a model system prompt)    |
| "Task"          | Async/long-running tool-style op (experimental)        |
| "Roots"         | URI scope hint, *not* an enforced permission           |
| "Sampling"      | Server asking the Host's LLM to generate text          |
| "Elicitation"   | Server asking the user a structured question           |
| "SSE transport" | Legacy — use Streamable HTTP                           |
| "Capabilities"  | Negotiated feature flags from `initialize`             |
| "DCR" / "CIMD"  | Client registration; CIMD is now the default, DCR is MAY |
| "DPoP"          | Sender-constrained tokens (SEP-1932, in review)        |

## Sources

- [MCP Specification (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25)
- [MCP Changelog](https://modelcontextprotocol.io/specification/2025-11-25/changelog)
- [2026 MCP Roadmap](https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/)
- [MCP Architecture](https://modelcontextprotocol.io/docs/learn/architecture)
- [MCP Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [MCP Authorization](https://modelcontextprotocol.io/docs/tutorials/security/authorization)
- [WorkOS — Tools, Resources, Prompts, Sampling, Roots, Elicitation](https://workos.com/blog/mcp-features-guide)
- [MCPcat — Server vs Client vs Host](https://mcpcat.io/blog/mcp-server-client-host/)
- [fka.dev — Why MCP deprecated SSE](https://blog.fka.dev/blog/2025-06-06-why-mcp-deprecated-sse-and-go-with-streamable-http/)
- [Obsidian Security — OAuth pitfalls in MCP](https://www.obsidiansecurity.com/blog/when-mcp-meets-oauth-common-pitfalls-leading-to-one-click-account-takeover)
- [Auth0 — CIMD vs DCR for MCP](https://auth0.com/blog/cimd-vs-dcr-mcp-registration/)
