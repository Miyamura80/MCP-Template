import { headers } from "next/headers";

const FALLBACK_ORIGIN = "http://localhost:3000";

/**
 * Resolve the absolute origin (scheme + host) the site is served from.
 *
 * Discovery files (robots.txt, sitemap.xml, schema map, agent files) must emit
 * absolute URLs. Rather than hardcode a domain into this template, we prefer
 * explicit configuration and otherwise derive the origin from the incoming
 * request so the emitted URLs resolve on whatever host actually serves them.
 */
export async function getSiteOrigin(): Promise<string> {
  const configured = process.env.NEXT_PUBLIC_SITE_URL;
  if (configured) return configured.replace(/\/+$/, "");

  const vercel =
    process.env.VERCEL_PROJECT_PRODUCTION_URL ?? process.env.VERCEL_URL;
  if (vercel) return `https://${vercel.replace(/\/+$/, "")}`;

  const h = await headers();
  const host = h.get("x-forwarded-host") ?? h.get("host");
  if (host) {
    const proto = h.get("x-forwarded-proto") ?? "https";
    return `${proto}://${host}`;
  }

  return FALLBACK_ORIGIN;
}

/**
 * The advertised MCP server endpoint. Configurable for deployments that mount
 * the FastMCP streamable-HTTP server on a different host than the docs site
 * (the template mounts it at `/mcp` on the API server). Falls back to
 * `<origin>/mcp`.
 */
export async function getMcpEndpoint(): Promise<string> {
  const configured = process.env.NEXT_PUBLIC_MCP_URL;
  if (configured) return configured.replace(/\/+$/, "");
  const origin = await getSiteOrigin();
  return `${origin}/mcp`;
}
