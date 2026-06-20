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

export async function GET() {
  const origin = await getSiteOrigin();

  // Use a Set so duplicate URLs across locale fallbacks are de-duplicated.
  const urls = new Set<string>();
  for (const lang of i18n.languages) {
    urls.add(`${origin}/${lang}`);
    for (const page of source.getPages(lang)) {
      urls.add(`${origin}${page.url}`);
    }
  }

  const body = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${[...urls]
  .map((u) => `  <url>\n    <loc>${xmlEscape(u)}</loc>\n  </url>`)
  .join("\n")}
</urlset>
`;

  return new Response(body, {
    headers: {
      "Content-Type": "application/xml; charset=utf-8",
      "Cache-Control": "public, max-age=3600, s-maxage=3600",
    },
  });
}
