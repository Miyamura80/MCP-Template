/**
 * Agent / LLM discovery content, composed from the single landing-page config
 * (`src/config/landing.ts`) so it never drifts from the marketing copy.
 *
 * Consumed by the discovery endpoints under `src/pages/` (robots.txt,
 * sitemap.xml, schemamap.xml, llms.txt, llms-full.txt, agents.md, and the
 * /.well-known/* documents).
 */
import {
  compatibility,
  connect,
  faq,
  features,
  getStarted,
  site,
} from "../config/landing";

/** Canonical origin with no trailing slash. */
export const ORIGIN = site.url.replace(/\/+$/, "");

/** Absolute URL for a site-relative path. */
export function abs(path: string): string {
  return `${ORIGIN}${path.startsWith("/") ? path : `/${path}`}`;
}

/** AI / LLM crawler user-agents this site explicitly welcomes. */
export const AI_USER_AGENTS = [
  "GPTBot",
  "ChatGPT-User",
  "OAI-SearchBot",
  "ClaudeBot",
  "Claude-User",
  "Claude-SearchBot",
  "anthropic-ai",
  "PerplexityBot",
  "Perplexity-User",
  "Google-Extended",
  "Applebot-Extended",
  "CCBot",
  "Amazonbot",
  "Meta-ExternalAgent",
  "cohere-ai",
  "DuckAssistBot",
  "YouBot",
];

/** HTML pages an agent or crawler should index. */
export const SITE_PAGES = ["/", "/privacy", "/terms"];

/** llms.txt — a short, link-first index (https://llmstxt.org). */
export function llmsIndexText(): string {
  const lines: string[] = [
    `# ${site.name}`,
    "",
    `> ${site.description}`,
    "",
    "## Links",
    "",
    `- [Documentation](${site.docsUrl})`,
    `- [Source code](${site.githubUrl})`,
    `- [MCP endpoint (streamable HTTP)](${site.mcpUrl})`,
    `- [Full text for LLMs](${abs("/llms-full.txt")})`,
    `- [Agent guide](${abs("/agents.md")})`,
    "",
    "## FAQ",
    "",
    ...faq.items.map((item) => `- ${item.q}`),
    "",
  ];
  return lines.join("\n");
}

/** llms-full.txt — the full, self-contained corpus for LLM ingestion. */
export function llmsFullText(): string {
  const lines: string[] = [
    `# ${site.name}`,
    "",
    `> ${site.tagline}`,
    "",
    site.description,
    "",
    "## What it is",
    "",
    `${site.name} is a Model Context Protocol (MCP) server. It exposes one`,
    "shared service registry identically over three transports — a CLI, an MCP",
    "server (streamable HTTP), and a plain HTTP API — so any agent that speaks",
    "MCP can call the same typed tools with no duplicated logic.",
    "",
    "- MCP endpoint (streamable HTTP): " + site.mcpUrl,
    "- Server name (for client configs): " + site.serverName,
    "- Documentation: " + site.docsUrl,
    "- Source code: " + site.githubUrl,
    "",
    `## ${features.heading}`,
    "",
    features.subhead,
    "",
    ...features.items.flatMap((f) => [`### ${f.title}`, "", f.body, ""]),
    `## ${getStarted.heading}`,
    "",
    getStarted.subhead,
    "",
    ...getStarted.transports.flatMap((t) => [
      `### ${t.label}: ${t.setupTitle}`,
      "",
      t.setupBody,
      "",
      t.callBody,
      "",
    ]),
    "## Supported clients",
    "",
    compatibility.clients.map((c) => c.name).join(", ") + ".",
    "",
    "## Connecting an MCP client",
    "",
    `Server URL: ${connect.mcpUrl}  ·  server name: ${connect.serverName}`,
    "",
    ...connect.targets.flatMap((target) => {
      const how =
        target.method === "deeplink"
          ? "One-click install (deep link supported)."
          : (target.steps ?? []).map((s) => `  - ${s}`).join("\n");
      return [`- ${target.name}: `, how, ""];
    }),
    "## FAQ",
    "",
    ...faq.items.flatMap((item) => [`### ${item.q}`, "", item.a, ""]),
  ];
  return lines.join("\n");
}

/** agents.md — agent onboarding guide. */
export function agentsMarkdown(): string {
  const lines: string[] = [
    "# Agents",
    "",
    `${site.name} — ${site.tagline}.`,
    "",
    site.description,
    "",
    "## How agents should use this site",
    "",
    `- **Full corpus for LLMs:** ${abs("/llms-full.txt")}`,
    `- **Index for LLMs:** ${abs("/llms.txt")}`,
    `- **Sitemap:** ${abs("/sitemap.xml")}`,
    `- **Schema Map feed:** ${abs("/schemamap.xml")}`,
    `- **MCP discovery:** ${abs("/.well-known/mcp")}`,
    `- **Skills manifest:** ${abs("/.well-known/agent-skills")}`,
    "",
    "## MCP server",
    "",
    "The Model Context Protocol server is the primary programmatic surface.",
    "",
    `- **Endpoint:** ${site.mcpUrl} (streamable HTTP)`,
    `- **Server name:** ${site.serverName}`,
    `- **Documentation:** ${site.docsUrl}`,
    "",
    "Connect by adding the endpoint URL to any MCP client (Claude, Cursor, VS",
    "Code, ChatGPT, Goose, …). Your agent then discovers and calls the tools",
    "automatically with typed inputs and structured output.",
    "",
  ];
  return lines.join("\n");
}
