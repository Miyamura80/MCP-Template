import type { APIRoute } from "astro";
import { SITE_PAGES, abs } from "../utils/agent-content";

function xmlEscape(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export const GET: APIRoute = () => {
  const body = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${SITE_PAGES.map(
    (p) => `  <url>\n    <loc>${xmlEscape(abs(p))}</loc>\n  </url>`,
  ).join("\n")}
</urlset>
`;
  return new Response(body, {
    headers: { "Content-Type": "application/xml; charset=utf-8" },
  });
};
