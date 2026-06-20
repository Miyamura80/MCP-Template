import { source } from "@/lib/source";
import { i18n } from "@/lib/i18n";
import { getSiteOrigin } from "@/lib/site";
import { type NextRequest } from "next/server";

export const revalidate = false;

/**
 * llms.txt index (https://llmstxt.org): a curated, link-first map of the docs
 * for LLMs. The full concatenated corpus lives at /llms-full.txt.
 */
export async function GET(req: NextRequest) {
  const origin = await getSiteOrigin();
  const lang = req.nextUrl.searchParams.get("lang") ?? i18n.defaultLanguage;
  const pages = source.getPages(lang);

  const lines: string[] = [
    "# MCP Template",
    "",
    "> Super-opinionated Python template that ships one codebase over three",
    "> interfaces (CLI, MCP server, HTTP API) backed by a shared service registry.",
    "",
    `Full text for LLMs: ${origin}/llms-full.txt`,
    "",
    "## Documentation",
    "",
  ];

  for (const page of pages) {
    const title = page.data.title ?? page.url;
    const description = page.data.description
      ? `: ${page.data.description}`
      : "";
    lines.push(`- [${title}](${origin}${page.url}.mdx)${description}`);
  }

  lines.push("");

  return new Response(lines.join("\n"), {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=3600, s-maxage=3600",
    },
  });
}
