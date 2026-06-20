/**
 * Generate the pre-connect discovery documents from src/config/landing.ts.
 *
 * Runs before `astro build` (see package.json `build`). Output lands in
 * `public/` so Astro copies it verbatim into `dist/`. `sirv` serves
 * `/.well-known/*` even though it is a dotfolder - well-known paths are exempt
 * from both its dotfile filter and the `--single` SPA fallback - so the static
 * card is reachable in production with no extra flags.
 *
 * Single source of truth: edit branding in `landing.ts` (`site` + `serverCard`),
 * never hand-edit the generated JSON. Run `bun run gen:discovery` to refresh.
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

// SEP-2127 Server Card (the pre-connect discovery document). No `$schema`: the
// draft server-card schema is not published yet (the URL 404s), so emitting it
// would only break validators - matching the bare /.well-known/mcp.json doc.
const card = {
  name: serverCard.name,
  version: serverCard.version,
  title: site.name,
  description: serverCard.description,
  websiteUrl: site.url,
  repository,
  icons: [icon],
  remotes,
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
