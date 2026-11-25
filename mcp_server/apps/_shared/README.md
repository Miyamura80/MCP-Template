# Shared MCP App source

Plain TypeScript that every app under `mcp_server/apps/*/` imports by relative
path (`../../_shared/...`). Vite inlines it into each app's single-file
`dist/mcp-app.html`, so nothing here ships on its own.

- `mcpApp.ts`: the `McpAppSurface` type (the part of the ext-apps `App` we use),
  `McpAppLike<...>` to pick an app's members, and `bufferToolResults`.
- `toolResult.ts`: `extractStructuredContent` and `errMsg`.
- `appContract.ts`: the body of each app's `appContract.test.ts`.
- `canvasStubs.ts`: jsdom stubs for apps that load pdfjs-dist.
- `tsconfig.base.json`, `viteShared.ts`: config every app extends or spreads.
- `*.test.ts`: run by every app's `bun run test`.

Rules:

- **No package imports.** This folder has no `package.json` or `node_modules`,
  so an import of `react` or `@modelcontextprotocol/ext-apps` here would not
  resolve. Anything that needs a package stays in the app.
- The contract stays per app: each app installs its own ext-apps version, so
  each app's `bun run test` type-checks the real `App` against `McpAppSurface`.
- A `_`-prefixed folder under `apps/` is never an app: the server does not
  register it as a `ui://` resource, and `make build_apps` and CI skip it
  because it has no `package.json`.
