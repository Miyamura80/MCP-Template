/**
 * Generate the pre-connect discovery documents from src/config/landing.ts.
 *
 * Runs before `astro build` (see package.json `build`). Output lands in
 * `public/` so Astro copies it verbatim into `dist/`. `sirv` serves
 * `/.well-known/*` even though it is a dotfolder - well-known paths are exempt
 * from both its dotfile filter and the `--single` SPA fallback - so the static
 * card is reachable in production. The `start` script runs `sirv` with `--cors`
 * so registries/clients can fetch these docs cross-origin (SEP-2127 expects the
 * server card to be CORS-readable), matching the API route's CORS header.
 *
 * Single source of truth: edit branding in `landing.ts` (`site` + `serverCard`),
 * never hand-edit the generated JSON. Run `bun run gen:discovery` to refresh.
 *
 * The `tools[]` surface is NOT branding - its real source of truth is the Python
 * `@service` registry. We snapshot it from the running API at build time (same
 * strategy as `gen-openapi.ts`) so the static card can't drift from the live
 * server; `serverCard.tools` in `landing.ts` is only the offline-build fallback.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { serverCard, site } from "../src/config/landing.ts";

const here = dirname(fileURLToPath(import.meta.url));
const publicDir = join(here, "..", "public");

const icon = {
  src: new URL("/favicon.svg", site.url).href,
  mimeType: "image/svg+xml",
  sizes: ["any"],
};
const repository = { url: site.githubUrl, source: serverCard.repositorySource };
const remotes = [{ type: "streamable-http", url: site.mcpUrl }];

interface Tool {
  name: string;
  description: string;
}

/**
 * Resolve the tool surface from the live registry, falling back to the committed
 * list in `landing.ts` on an offline build.
 *
 * The API mounts the same FastAPI app as `/mcp` and serves the authoritative,
 * registry-derived `tools[]` at `/.well-known/mcp/server-card.json`. Fetching it
 * server-to-server (no CORS) keeps the static card in step with the real tools
 * without hand-maintaining descriptions in two places.
 */
async function resolveTools(): Promise<readonly Tool[]> {
  const fallback = serverCard.tools;
  const url = new URL("/.well-known/mcp/server-card.json", site.apiUrl).href;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15_000);
  try {
    const res = await fetch(url, { signal: controller.signal });
    if (!res.ok) {
      console.warn(`⚠ ${url} returned ${res.status}; using fallback tool list`);
      return fallback;
    }
    const live = (await res.json()) as { tools?: unknown };
    const tools = live.tools;
    const usable =
      Array.isArray(tools) &&
      tools.length > 0 &&
      tools.every((t) => {
        const o = t as Record<string, unknown>;
        return typeof o.name === "string" && typeof o.description === "string";
      });
    if (!usable) {
      console.warn(`⚠ ${url} had no usable tools[]; using fallback tool list`);
      return fallback;
    }
    console.log(`✓ tool surface from ${url} (${(tools as Tool[]).length} tools)`);
    return (tools as Tool[]).map((t) => ({ name: t.name, description: t.description }));
  } catch (err) {
    console.warn(`⚠ could not fetch ${url} (${String(err)}); using fallback tool list`);
    return fallback;
  } finally {
    clearTimeout(timer);
  }
}

const tools = await resolveTools();

// SEP-2127 Server Card (the pre-connect discovery document). No `$schema`: the
// draft server-card schema is not published yet (the URL 404s), so emitting it
// would only break validators - matching the bare /.well-known/mcp.json doc.
// `serverUrl` (flat endpoint) and `tools[]` let agents preview where to connect
// and what the server can do before opening a transport; `remotes` carries the
// same endpoint in the SEP-2127 / registry shape.
const card = {
  name: serverCard.name,
  version: serverCard.version,
  title: site.name,
  description: serverCard.description,
  websiteUrl: site.url,
  serverUrl: site.mcpUrl,
  repository,
  icons: [icon],
  remotes,
  tools,
};

// MCP registry server.json (same identity, registry schema).
const registry = {
  $schema:
    "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
  name: serverCard.name,
  title: site.name,
  description: serverCard.description,
  version: serverCard.version,
  websiteUrl: site.url,
  repository,
  icons: [icon],
  remotes,
};

function write(relPath: string, data: unknown): void {
  const out = join(publicDir, relPath);
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, JSON.stringify(data, null, 2) + "\n");
  console.log(`✓ generated public/${relPath}`);
}

write(".well-known/mcp/server-card.json", card);
write("server.json", registry);
