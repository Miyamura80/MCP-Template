import type { APIRoute } from "astro";
import { SITE_PAGES, abs } from "../utils/agent-content";

function xmlEscape(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/**
 * NLWeb / Schema Map feed: a sitemap-format index of URLs exposing structured,
 * schema.org-style content for agents, plus the machine-readable corpora.
 * Referenced from the `Schemamap:` directive in robots.txt.
 */
export const GET: APIRoute = () => {
  const body = `<?xml version="1.0" encoding="UTF-8"?>
<schemamap xmlns="https://schemamap.io/schema/0.1">
  <feed>
    <llms-full>${xmlEscape(abs("/llms-full.txt"))}</llms-full>
    <llms>${xmlEscape(abs("/llms.txt"))}</llms>
  </feed>
${SITE_PAGES.map(
    (p) => `  <url>\n    <loc>${xmlEscape(abs(p))}</loc>\n  </url>`,
  ).join("\n")}
</schemamap>
`;
  return new Response(body, {
    headers: { "Content-Type": "application/xml; charset=utf-8" },
  });
};
