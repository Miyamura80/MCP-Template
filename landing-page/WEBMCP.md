# WebMCP (agent-navigable landing page)

The landing page exposes its own actions to in-browser AI agents via the W3C
[WebMCP](https://github.com/webmachinelearning/webmcp) `navigator.modelContext`
API, so an agent browsing the site calls structured tools instead of scraping
the DOM.

All of this lives in **`src/components/WebMcp.astro`**, loaded once from
`src/layouts/Base.astro` so it runs on every page.

## Tools

Four tools are registered, all sourced from `src/config/landing.ts` (the same
single source of truth the visible UI reads), so they never drift from the page:

| Tool                       | Input        | Returns                                                        |
| -------------------------- | ------------ | ------------------------------------------------------------- |
| `get_mcp_endpoint`         | –            | The streamable-HTTP MCP server URL + server name, docs, repo. |
| `list_supported_clients`   | –            | Each client and whether it's one-click or manual install.     |
| `get_install_instructions` | `client_id`  | The rebuilt one-click deep link, or the paste-the-URL steps.  |
| `answer_faq`               | `query`      | Best word-overlap match from the FAQ (`faq.items`).           |

To add or change a tool, edit the relevant config in `src/config/landing.ts`
first (`site`, `connect.targets`, `faq.items`), then register/adjust the tool in
`WebMcp.astro`. Keep tools config-driven so the agent surface and the visible UI
stay in lockstep.

## Progressive enhancement

It's **feature-detected and a no-op** in browsers without the API (everything
except Edge 147+ / Chrome's origin trial as of mid-2026), so normal visitors are
unaffected and there's nothing to configure. The page ships statically; the
tools simply aren't registered where the API is absent.

## Spec caveats

WebMCP is an early, fast-moving W3C Draft Community Group Report. The component
is deliberately defensive about the parts still in flux:

- **API object:** detects both `navigator.modelContext` (shipped in browsers)
  and `document.modelContext` (used by the spec draft).
- **Registration:** prefers the batch `provideContext({ tools })` and falls back
  to per-tool `registerTool(tool, { signal })`.
- **Result shape:** each `execute` returns the spec's
  `{ content: [{ type: "text", text }] }`, plus a `structuredContent` payload.

Re-verify against the current spec before extending it — behaviors (transports,
method shapes, lifecycle) change frequently.

## References

- Spec / explainer: https://github.com/webmachinelearning/webmcp
- `provideContext` vs `registerTool` semantics: https://github.com/webmachinelearning/webmcp/issues/101
