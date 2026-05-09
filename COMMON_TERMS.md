# MCP Common Terms

Glossary of frequently confused **Model Context Protocol** terms. Consult this
before naming or designing new MCP-related code.

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

Decide by: does it mutate state → Tool; should the app inject it → Resource;
should the user pick it → Prompt. Don't expose read-only data as a Tool.

## 3. Sampling, Roots, Elicitation (client primitives)

- **Sampling** — Server asks the Host's LLM to generate a completion.
- **Roots** — Client tells Server which `file://` paths it's scoped to.
  *Informational, not a permission boundary.* Enforce auth separately.
- **Elicitation** — Server asks the user for structured input via JSON Schema.

## 4. Resources vs. Roots

Both use URIs but differ:
- **Resource** = data the Server exposes (any scheme: `file://`, `db://`, custom).
- **Root** = filesystem boundary the Client declares (always `file://`).

Resources answer "what can I read?"; Roots answer "where am I scoped to?".

## 5. Transports

- **stdio** — local child process; simplest, fastest. Use for local tools.
- **Streamable HTTP** — single endpoint, request/response + SSE streaming,
  OAuth 2.1. Use for remote.
- **SSE** — *deprecated*; legacy two-endpoint setup. Don't use for new servers.

## 6. JSON-RPC handshake

1. Client → `initialize` (protocolVersion, capabilities, clientInfo).
2. Server → response with its protocolVersion + capabilities.
3. Client → `notifications/initialized`.

**Capabilities** are negotiated feature flags — don't call methods whose
capability wasn't advertised. Mismatched `protocolVersion` terminates the
connection.

## 7. OAuth 2.1 pitfalls

- MCP server is a **resource server**, not the authorization server (token factory).
- Sessions are anonymous unless wired through OAuth.
- `resource` parameter must match the AS's expected URL **exactly** (trailing slash matters) — mismatches cause silent 401s.
- Split scopes per tool/capability; avoid catch-all `admin` scopes.
- Persist tokens so users don't re-consent on every Host restart (a known account-takeover vector).

## 8. Cheat-sheet

| If you hear…    | It means…                                              |
|-----------------|--------------------------------------------------------|
| "MCP client"    | Either the Host or its in-Host connector — clarify     |
| "MCP tool"      | Model-callable function; side effects allowed          |
| "MCP resource"  | Read-only data exposed by URI                          |
| "MCP prompt"    | User-triggered template (not a model system prompt)    |
| "Roots"         | Filesystem scope hint, *not* a permission              |
| "Sampling"      | Server asking the Host's LLM to generate text          |
| "Elicitation"   | Server asking the user a structured question           |
| "SSE transport" | Legacy — use Streamable HTTP                           |
| "Capabilities"  | Negotiated feature flags from `initialize`             |

## Sources

- [MCP Specification](https://modelcontextprotocol.io/specification/2025-11-25)
- [MCP Architecture](https://modelcontextprotocol.io/docs/learn/architecture)
- [MCP Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [MCP Authorization](https://modelcontextprotocol.io/docs/tutorials/security/authorization)
- [WorkOS — Tools, Resources, Prompts, Sampling, Roots, Elicitation](https://workos.com/blog/mcp-features-guide)
- [MCPcat — Server vs Client vs Host](https://mcpcat.io/blog/mcp-server-client-host/)
- [fka.dev — Why MCP deprecated SSE](https://blog.fka.dev/blog/2025-06-06-why-mcp-deprecated-sse-and-go-with-streamable-http/)
- [Obsidian Security — OAuth pitfalls in MCP](https://www.obsidiansecurity.com/blog/when-mcp-meets-oauth-common-pitfalls-leading-to-one-click-account-takeover)
