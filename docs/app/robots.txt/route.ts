import { getSiteOrigin } from "@/lib/site";

export const revalidate = false;

/**
 * AI / LLM crawlers and agent user-agents this documentation explicitly
 * welcomes. Listing them by name (rather than relying on `User-agent: *`)
 * is what agent-readiness audits look for: an intentional AI access policy.
 */
const AI_USER_AGENTS = [
  // OpenAI
  "GPTBot",
  "ChatGPT-User",
  "OAI-SearchBot",
  // Anthropic
  "ClaudeBot",
  "Claude-User",
  "Claude-SearchBot",
  "anthropic-ai",
  // Perplexity
  "PerplexityBot",
  "Perplexity-User",
  // Google / Apple model training
  "Google-Extended",
  "Applebot-Extended",
  // Others
  "CCBot",
  "Amazonbot",
  "Meta-ExternalAgent",
  "cohere-ai",
  "DuckAssistBot",
  "YouBot",
  "Bytespider",
];

export async function GET() {
  const origin = await getSiteOrigin();

  const lines: string[] = [
    "# Default policy: everything is indexable.",
    "User-agent: *",
    "Allow: /",
    "",
    "# AI / LLM crawlers and agents are explicitly welcome to read and",
    "# index this documentation. See /llms-full.txt and /agents.md.",
  ];

  for (const ua of AI_USER_AGENTS) {
    lines.push("");
    lines.push(`User-agent: ${ua}`);
    lines.push("Allow: /");
  }

  lines.push("");
  lines.push(`Sitemap: ${origin}/sitemap.xml`);
  lines.push("");
  lines.push("# NLWeb / Schema Map structured-data feed.");
  lines.push("# https://schemamap.io  ·  https://github.com/nlweb-ai/NLWeb");
  lines.push(`Schemamap: ${origin}/schemamap.xml`);
  lines.push("");

  return new Response(lines.join("\n"), {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=3600, s-maxage=3600",
    },
  });
}
