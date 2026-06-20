import { createI18nMiddleware } from "fumadocs-core/i18n/middleware";
import { i18n } from "@/lib/i18n";

export default createI18nMiddleware(i18n);

export const config = {
  // Exclude framework internals, static assets, and the agent-discovery files
  // (robots.txt, sitemap.xml, llms*.txt, agents.md, schemamap.xml, .well-known/*)
  // so they are served directly instead of being redirected to a locale prefix.
  matcher: [
    "/((?!api|_next/static|_next/image|favicon.ico|icon-light.png|icon-dark.png|og/|robots\\.txt|sitemap\\.xml|schemamap\\.xml|llms\\.txt|llms-full\\.txt|agents\\.md|\\.well-known/).*)",
  ],
};
