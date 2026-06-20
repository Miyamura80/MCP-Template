import { source } from "@/lib/source";
import { i18n } from "@/lib/i18n";
import { getSiteOrigin } from "@/lib/site";

export const revalidate = false;

function xmlEscape(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/**
 * NLWeb / Schema Map feed: a sitemap-format index of URLs that expose
 * structured, schema.org-style content for agents to consume. Referenced from
 * the `Schemamap:` directive in robots.txt.
 *
 * Each documentation page is also offered as raw Markdown (`<url>.mdx`) and the
 * whole corpus is available at /llms-full.txt for direct LLM ingestion.
 */
export async function GET() {
  const origin = await getSiteOrigin();

  const entries: string[] = [];
  const seen = new Set<string>();
  for (const lang of i18n.languages) {
    for (const page of source.getPages(lang)) {
      const loc = `${origin}${page.url}`;
      if (seen.has(loc)) continue;
      seen.add(loc);
      entries.push(
        `  <url>\n    <loc>${xmlEscape(loc)}</loc>\n    <markdown>${xmlEscape(
          `${loc}.mdx`,
        )}</markdown>\n  </url>`,
      );
    }
  }

  const body = `<?xml version="1.0" encoding="UTF-8"?>
<schemamap xmlns="https://schemamap.io/schema/0.1">
  <feed>
    <llms-full>${xmlEscape(`${origin}/llms-full.txt`)}</llms-full>
    <llms>${xmlEscape(`${origin}/llms.txt`)}</llms>
  </feed>
${entries.join("\n")}
</schemamap>
`;

  return new Response(body, {
    headers: {
      "Content-Type": "application/xml; charset=utf-8",
      "Cache-Control": "public, max-age=3600, s-maxage=3600",
    },
  });
}
