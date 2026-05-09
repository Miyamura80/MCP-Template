# MCP Common Terms

Glossary of frequently confused terms in the **Model Context Protocol (MCP)**.
MCP is an open protocol (introduced by Anthropic, Nov 2024) that standardizes
how LLM applications connect to external data and tools, solving the *N×M
integration problem*.

This file is the canonical reference for terminology used in this repository.
When you encounter ambiguity (e.g., "is this a tool or a resource?"), consult
this document before adding new code.

---

## 1. Architecture roles: Host vs. Client vs. Server

These three terms are the most commonly confused. They are NOT synonyms.

| Term       | Role                                        | Example                                    |
|------------|---------------------------------------------|--------------------------------------------|
| **Host**   | The user-facing AI application that owns the LLM. The "brain". | Claude Desktop, Cursor, ChatGPT app, an IDE |
| **Client** | A protocol-level component *inside* the Host. One Client talks to exactly one Server, 1:1. | The connector inside Claude Desktop that speaks to a single MCP server |
| **Server** | An external program that exposes data/capabilities over MCP. | This repo's `mcp_server/`, a GitHub MCP server, a Postgres MCP server |

Mental model: a single **Host** spawns *many* **Clients**, each connected to
*one* **Server**. Users interact with the Host; Clients are invisible plumbing.

> Pitfall: docs sometimes say "MCP client" when they mean "the Host application".
> When in doubt, ask: *is this a user-facing app (Host) or a 1:1 protocol
> connector (Client)?*

---

## 2. Server primitives: Tools vs. Resources vs. Prompts

All three are things a **Server** exposes, but they differ in *who controls
invocation* and *whether they have side effects*.

| Primitive  | Controlled by | Side effects?       | Analogy                  |
|------------|---------------|---------------------|--------------------------|
| **Tool**   | Model         | Yes (can mutate)    | A function call          |
| **Resource** | Application | No (read-only)      | A file or GET endpoint   |
| **Prompt** | User          | No (just a template)| A slash-command template |

Decision flow:

1. Does it create / modify / delete state? → **Tool**.
2. Should the application inject it as background context? → **Resource**.
3. Should the user explicitly pick it from a menu? → **Prompt**.

> Pitfall: developers often expose every capability as a Tool because that's
> what models "see." If the data is read-only context, prefer a **Resource**;
> the host can stream it without burning a tool-call round-trip.

---

## 3. Client primitives: Sampling, Roots, Elicitation

These are features the **Client** offers *back to the Server* — the protocol
is bidirectional. Servers may request them, but Clients gate them.

- **Sampling** — Server asks the Client's LLM to generate a completion.
  Enables agentic / recursive flows where the server delegates reasoning.
- **Roots** — Client tells the Server which filesystem paths it is scoped to.
  Always `file://` URIs. *Informational, not enforced* — Roots are NOT a
  permission boundary; they are a hint. Real authorization must be enforced
  separately.
- **Elicitation** — Server asks the user (via the Client) for additional
  structured input mid-flow, using a JSON Schema.

> Pitfall: do not treat **Roots** as a sandbox. A malicious or buggy server
> can still attempt access outside the declared roots — Roots tell the server
> *where to look*, not *where it is forbidden from looking*.

---

## 4. Resources vs. Roots (very commonly confused)

Both involve URIs but mean different things.

- **Resource**: a piece of data the Server *exposes*. Any URI scheme
  (`file://`, `db://`, `docs://`, custom). Read-only data the model can pull in.
- **Root**: a filesystem boundary the Client *declares* to the Server.
  Always `file://`. About scoping, not data.

Resources answer "what can I read?". Roots answer "where am I allowed to look?".

---

## 5. Transports: stdio vs. Streamable HTTP vs. SSE

The protocol layer is JSON-RPC 2.0. The transport is how those messages move.

| Transport          | Use when                                              | Status         |
|--------------------|-------------------------------------------------------|----------------|
| **stdio**          | Local server, spawned as a child process. Simplest, fastest, single-client. | Current         |
| **Streamable HTTP**| Remote server, multi-client, OAuth-secured. Single endpoint that supports both request/response and SSE streaming. | **Current (preferred for remote)** |
| **SSE** (legacy)   | Older remote setup with two endpoints (POST + SSE). Compatibility issues with modern HTTP infrastructure. | **Deprecated** — prefer Streamable HTTP |

> Pitfall: blogs/tutorials written before mid-2025 often describe SSE as the
> remote transport. New servers should use Streamable HTTP; only keep SSE for
> backward compatibility.

---

## 6. JSON-RPC handshake & capability negotiation

Every connection opens with a strict three-step handshake:

1. Client → Server: `initialize` (sends `protocolVersion`, `capabilities`, `clientInfo`).
2. Server → Client: response with its own `protocolVersion`, `capabilities`, `serverInfo`.
3. Client → Server: `notifications/initialized` (signals readiness).

**Capabilities** are the contract: each side advertises which features
(tools, resources, prompts, sampling, roots, elicitation, …) it supports.
Don't call a method whose capability wasn't negotiated.

If `protocolVersion` is incompatible, the connection terminates — the
versions are not auto-coerced.

---

## 7. Authorization: OAuth 2.1 nuances

MCP authorization is OAuth 2.1, but there are recurring footguns.

- **The MCP server is a *resource server*, not an authorization server.**
  The token factory is a separate AS; the MCP server only validates tokens.
  Many tutorials conflate the two.
- **Sessions are anonymous by default.** A session cookie does NOT carry a
  user identity unless you wire it through OAuth.
- **`resource` parameter must match exactly.** Use the base URL *with*
  trailing slash; mismatches between client `resource=` and AS validation
  cause silent 401s.
- **Avoid catch-all scopes.** Split scopes per tool/capability. Don't ship
  an `admin` scope — it gives auditors no signal.
- **Persist tokens.** Otherwise users re-auth every server on each Host
  restart, which trains them to click through consent screens — a known
  vector for 1-click account takeover.

---

## 8. Quick disambiguation cheat-sheet

| If you hear…           | It probably means…                                   |
|------------------------|------------------------------------------------------|
| "MCP client"           | Either the Host *or* the in-Host connector — clarify |
| "MCP tool"             | A model-callable function (side effects allowed)     |
| "MCP resource"         | Read-only data exposed by URI                        |
| "MCP prompt"           | A user-triggered template, not a model system prompt |
| "Roots"                | Filesystem scope hint, *not* a permission            |
| "Sampling"             | Server asking the Host's LLM to generate text        |
| "Elicitation"          | Server asking the user a structured question         |
| "SSE transport"        | Legacy — use Streamable HTTP instead                 |
| "Capabilities"         | Negotiated feature flags from the `initialize` call  |

---

## Sources

- [Model Context Protocol — Specification](https://modelcontextprotocol.io/specification/2025-11-25)
- [Model Context Protocol — Architecture overview](https://modelcontextprotocol.io/docs/learn/architecture)
- [Model Context Protocol — Client concepts](https://modelcontextprotocol.io/docs/learn/client-concepts)
- [Model Context Protocol — Resources](https://modelcontextprotocol.io/specification/2025-06-18/server/resources)
- [Model Context Protocol — Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [Model Context Protocol — Authorization](https://modelcontextprotocol.io/docs/tutorials/security/authorization)
- [WorkOS — Tools, Resources, Prompts, Sampling, Roots, Elicitation](https://workos.com/blog/mcp-features-guide)
- [Microsoft — Tools vs Resources vs Prompts Explained](https://techcommunity.microsoft.com/blog/azuredevcommunityblog/mcp-demystified-tools-vs-resources-vs-prompts-explained-simply/4508057)
- [MCPcat — Server vs Client vs Host](https://mcpcat.io/blog/mcp-server-client-host/)
- [MCPcat — stdio vs SSE vs Streamable HTTP](https://mcpcat.io/guides/comparing-stdio-sse-streamablehttp/)
- [fka.dev — Why MCP deprecated SSE](https://blog.fka.dev/blog/2025-06-06-why-mcp-deprecated-sse-and-go-with-streamable-http/)
- [Obsidian Security — OAuth pitfalls in MCP](https://www.obsidiansecurity.com/blog/when-mcp-meets-oauth-common-pitfalls-leading-to-one-click-account-takeover)
- [Aaron Parecki — Let's fix OAuth in MCP](https://aaronparecki.com/2025/04/03/15/oauth-for-model-context-protocol)
- [HasMCP — MCP Glossary](https://hasmcp.com/glossary/)
