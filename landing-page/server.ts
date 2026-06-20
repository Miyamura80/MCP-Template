/**
 * Production static server for the landing page, with standards-compliant
 * `Accept: text/markdown` content negotiation on the canonical URL.
 *
 * Why this exists instead of plain `sirv-cli`:
 *   The site is a static Astro build. Agents increasingly probe pages with
 *   `Accept: text/markdown` (see acceptmarkdown.com) to fetch a clean,
 *   token-cheap representation of a page instead of scraping HTML. A bare
 *   static server always returns HTML, so we wrap `sirv` with a thin
 *   negotiation layer:
 *
 *     - GET/HEAD `/` (the canonical URL) with `Accept: text/markdown` ranked
 *       at or above `text/html` -> the page's markdown alternate
 *       (`buildAgentsMd`, the same doc linked via
 *       `<link rel="alternate" type="text/markdown" href="/agents.md">`).
 *     - Everything else -> static files via `sirv`.
 *
 * Crucially, *every* negotiable response advertises `Vary: Accept,
 * Accept-Encoding`. Without `Accept` in `Vary`, a shared CDN can cache the
 * HTML variant under the bare URL and then hand it to an agent that asked for
 * markdown (or vice-versa), depending on which representation populated the
 * cache first. We merge `Accept` into whatever `Vary` sirv emits (it already
 * sets `Vary: Accept-Encoding` for compressed responses) so caches key on both
 * dimensions.
 *
 * Run with bun (Railway's builder): `bun server.ts`. Honors `$PORT`.
 */
import { createServer, type ServerResponse } from "node:http";
import sirv from "sirv";

import { buildAgentsMd } from "./src/agent/content.ts";
import { site } from "./src/config/landing.ts";

const PORT = Number(process.env.PORT ?? 8080);

// Serve the Astro build. `single` keeps the SPA-style fallback to index.html.
// `setHeaders` re-adds the `Access-Control-Allow-Origin: *` that the old
// sirv-cli `--cors` flag provided, so registries/clients can fetch the
// /.well-known discovery docs cross-origin (SEP-2127). sirv@3 dropped the
// `cors` option, so we set the header ourselves.
const assets = sirv("dist", {
  single: true,
  gzip: true,
  brotli: true,
  setHeaders: (res) => {
    res.setHeader("Access-Control-Allow-Origin", "*");
  },
});

/**
 * Highest q-value the Accept header assigns to `type`, considering `type/*`
 * and `*\/*` wildcards. Returns -1 when nothing matches. Per RFC 9110 a media
 * range with no explicit `q` has q=1.
 */
function quality(accept: string, type: string): number {
  const [t, sub] = type.split("/");
  let best = -1;
  for (const range of accept.split(",")) {
    const parts = range.trim().split(";");
    const media = parts[0]?.trim().toLowerCase();
    if (!media) continue;
    let q = 1;
    for (const param of parts.slice(1)) {
      const [k, v] = param.split("=").map((s) => s.trim());
      if (k.toLowerCase() === "q") q = Number.parseFloat(v) || 0;
    }
    const [mt, ms] = media.split("/");
    const matches =
      media === type ||
      media === "*/*" ||
      (mt === t && ms === "*") ||
      (mt === "*" && ms === sub);
    if (matches && q > best) best = q;
  }
  return best;
}

/**
 * True when the client explicitly asked for `text/markdown` (not merely via a
 * `*\/*` catch-all) and ranks it at least as high as `text/html`. This keeps
 * default clients - browsers (`text/html,...,*\/*;q=0.8`) and bare `curl`
 * (`*\/*`) - on the HTML representation, while honoring agents that send
 * `Accept: text/markdown`.
 */
function wantsMarkdown(accept: string | undefined): boolean {
  if (!accept) return false;
  const md = explicitQuality(accept, "text/markdown");
  if (md <= 0) return false;
  return md >= quality(accept, "text/html");
}

/** Like `quality`, but ignores wildcard ranges - the type must be named. */
function explicitQuality(accept: string, type: string): number {
  let best = -1;
  for (const range of accept.split(",")) {
    const media = range.trim().split(";")[0]?.trim().toLowerCase();
    if (media !== type) continue;
    const qMatch = range.match(/;\s*q=([^;]+)/i);
    const q = qMatch ? Number.parseFloat(qMatch[1]) || 0 : 1;
    if (q > best) best = q;
  }
  return best;
}

/** Canonical URL = site root. Query string and trailing slash are ignored. */
function isCanonical(pathname: string): boolean {
  return pathname === "/" || pathname === "/index.html";
}

/**
 * First value of a (possibly comma-joined or repeated) header. `X-Forwarded-*`
 * can carry a chain like `host1, host2`; we want only the client-facing entry.
 * Returns undefined when empty.
 */
function firstHeaderToken(value: string | string[] | undefined): string | undefined {
  if (!value) return undefined;
  const raw = Array.isArray(value) ? value[0] : value;
  const first = raw.split(",")[0]?.trim();
  return first || undefined;
}

/**
 * Resolve the public origin for absolute links inside the markdown body,
 * honoring the proxy's forwarded headers and falling back to the configured
 * site URL.
 */
function originFor(host: string | undefined, proto: string | undefined): string {
  if (host) return `${proto || "https"}://${host}`;
  return new URL(site.url).origin;
}

const VARY = "Accept, Accept-Encoding";

/**
 * Wrap `res.setHeader` so any `Vary` sirv sets also carries `Accept`. sirv
 * emits `Vary: Accept-Encoding` for compressible responses; we fold `Accept`
 * in so caches never cross-serve the HTML and markdown representations.
 */
function ensureVaryAccept(res: ServerResponse): void {
  const original = res.setHeader.bind(res);
  res.setHeader = ((name: string, value: number | string | readonly string[]) => {
    if (String(name).toLowerCase() === "vary") {
      const tokens = new Set(
        String(value)
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      );
      tokens.add("Accept");
      return original("Vary", [...tokens].join(", "));
    }
    return original(name, value as never);
  }) as typeof res.setHeader;
}

const server = createServer((req, res) => {
  const method = req.method ?? "GET";
  // Parse only the request target for the pathname. The forwarded host is
  // untrusted and irrelevant to the path, so we use a fixed base - a malformed
  // host can no longer throw out of URL parsing and crash the handler.
  let pathname = "/";
  try {
    pathname = new URL(req.url ?? "/", "http://localhost").pathname;
  } catch {
    // noqa: keep serving; a bad request target just isn't a canonical match.
    pathname = "/";
  }

  const negotiable = isCanonical(pathname) && (method === "GET" || method === "HEAD");

  if (negotiable && wantsMarkdown(req.headers.accept)) {
    const host = firstHeaderToken(
      req.headers["x-forwarded-host"] ?? req.headers.host,
    );
    const proto = firstHeaderToken(req.headers["x-forwarded-proto"]);
    const body = buildAgentsMd(originFor(host, proto));
    const buf = Buffer.from(body, "utf-8");
    res.statusCode = 200;
    res.setHeader("Content-Type", "text/markdown; charset=utf-8");
    res.setHeader("Content-Length", String(buf.byteLength));
    res.setHeader("Vary", VARY);
    res.setHeader("Cache-Control", "public, max-age=0, must-revalidate");
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.end(method === "HEAD" ? undefined : buf);
    return;
  }

  // Only the canonical, content-negotiated route varies on Accept. Scope the
  // `Vary: Accept` here so static assets (which are never negotiated) keep a
  // single cache key and don't fragment. For the canonical HTML we both seed
  // `Vary` and fold `Accept` into whatever sirv later sets, so the HTML variant
  // is cached separately from the markdown one served above.
  if (negotiable) {
    ensureVaryAccept(res);
    res.setHeader("Vary", VARY);
  }

  assets(req, res, () => {
    res.statusCode = 404;
    res.end("Not found");
  });
});

server.listen(PORT, "0.0.0.0", () => {
  // eslint-disable-next-line no-console
  console.log(`landing-page serving dist/ on http://0.0.0.0:${PORT}`);
});
