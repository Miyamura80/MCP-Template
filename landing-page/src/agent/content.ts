/**
 * AI / agent discoverability content, generated from `config/landing.ts`.
 *
 * One source of truth (the landing config) drives every machine-readable
 * surface: llms.txt, llms-full.txt, agents.md and the in-page agent view.
 * Rebranding the site (editing landing.ts) keeps all of these in sync.
 */
import { site, hero, features, getStarted, faq, compatibility, connect, comparison } from "../config/landing";

/** Strip a trailing slash so we can safely append paths. */
function trimSlash(url: string): string {
  return url.replace(/\/+$/, "");
}

/** Concise llms.txt index (see https://llmstxt.org). */
export function buildLlmsTxt(origin: string): string {
  const o = trimSlash(origin);
  return `# ${site.name}

> ${site.description}

${hero.subhead}

## Connect over MCP
- [MCP endpoint](${site.mcpUrl}): Streamable-HTTP MCP server URL to add to your client. Server name: \`${site.serverName}\`.

## Documentation for LLMs
- [llms-full.txt](${o}/llms-full.txt): Full, expanded description of what ${site.name} is and how to use every transport.
- [agents.md](${o}/agents.md): Agent-oriented capability and skills summary.
- [How it compares](${o}/compare): ${site.name} vs other Gmail MCP servers (GongRzhe, Composio, Zapier/Pipedream, Google Workspace MCP).

## Resources
- [Documentation](${site.docsUrl})
- [Source code](${site.githubUrl})

## Optional
- [FAQ](${o}/#faq): Common questions about clients, transports, auth and self-hosting.
`;
}

/** Long-form llms-full.txt: everything an agent needs in one fetch. */
export function buildLlmsFullTxt(origin: string): string {
  const o = trimSlash(origin);
  const featureBlock = features.items
    .map((f) => `### ${f.title}\n${f.body}`)
    .join("\n\n");
  const transportBlock = getStarted.transports
    .map(
      (t) =>
        `### ${t.label}\n` +
        `**${t.setupTitle}** - ${t.setupBody}\n\n` +
        `**${t.callTitle}** - ${t.callBody}`,
    )
    .join("\n\n");
  const faqBlock = faq.items.map((i) => `### ${i.q}\n${i.a}`).join("\n\n");
  const clients = compatibility.clients.map((c) => c.name).join(", ");

  const pillarsBlock = comparison.pillars
    .map((p) => `- **${p.title}**: ${p.body}`)
    .join("\n");
  const competitorBlock = comparison.competitors
    .map(
      (c) =>
        `### ${site.name} vs ${c.name}\n` +
        `${c.headline} ${c.summary}\n\n` +
        `- Choose ${site.name} if: ${c.pickUs}\n` +
        `- Choose ${c.name} if: ${c.pickThem}\n` +
        `- Full comparison: ${o}/vs/${c.id}`,
    )
    .join("\n\n");

  return `# ${site.name} - ${site.tagline}

> ${site.description}

${hero.headline} ${hero.subhead}

- Website: ${site.url}
- MCP endpoint (streamable HTTP): ${site.mcpUrl}
- MCP server name: ${site.serverName}
- Documentation: ${site.docsUrl}
- Source code: ${site.githubUrl}

## What it is

${site.name} is a Model Context Protocol (MCP) server. It exposes a single
shared service registry over three interfaces - a CLI, an MCP server
(streamable HTTP), and a plain HTTP API - so the same typed tools behave
identically no matter how they are called. Any agent that speaks MCP can
discover and call its tools.

## ${getStarted.heading}

${getStarted.subhead}

${transportBlock}

## Features

${featureBlock}

## How it compares

${comparison.subhead}

What makes ${site.name} different (as of ${comparison.asOf}):
${pillarsBlock}

${competitorBlock}

See the full comparison and capability matrix at ${o}/compare.

## Compatible clients

Works with every MCP client, including: ${clients}.

## How to connect

1. Copy the MCP server URL: ${site.mcpUrl}
2. Add it to your client (server name \`${site.serverName}\`):
${connect.targets
  .map((t) =>
    t.method === "deeplink"
      ? `   - ${t.name}: one-click install (deep link supported).`
      : `   - ${t.name}: ${(t.steps ?? []).join(" → ")}`,
  )
  .join("\n")}
3. Your agent discovers the tools automatically and calls them with typed inputs.

## FAQ

${faqBlock}

## Machine-readable resources

- llms.txt: ${o}/llms.txt
- llms-full.txt: ${o}/llms-full.txt
- agents.md: ${o}/agents.md
- Agent skills (JSON): ${o}/.well-known/agent-skills/index.json
- MCP discovery (JSON): ${o}/.well-known/mcp.json
- Sitemap: ${o}/sitemap.xml
- Schema map: ${o}/schemamap.xml
`;
}

/** agents.md - agent/skills oriented capability summary. */
export function buildAgentsMd(origin: string): string {
  const o = trimSlash(origin);
  return `# ${site.name} - agent guide

${site.description}

This site documents an MCP server. Agents should connect over MCP to use its
tools rather than scraping this page.

## MCP server

- Endpoint (streamable HTTP): \`${site.mcpUrl}\`
- Server name: \`${site.serverName}\`
- Discovery: ${o}/.well-known/mcp.json

## How to use

1. Add the MCP endpoint above to your client.
2. List tools via the MCP \`tools/list\` method.
3. Call tools via \`tools/call\` with typed JSON arguments.

The same tools are also reachable over a CLI and a plain HTTP API; behaviour
is identical across all three transports.

## How it compares

${site.name} is a dedicated Gmail MCP, not a thin API wrapper or a generic
multi-app gateway. Its differentiators: interactive in-chat UI (MCP Apps),
one codebase exposed over CLI + MCP + HTTP, and an open-source, self-hostable
server. Head-to-head comparisons:
${comparison.competitors
  .map((c) => `- vs ${c.name}: ${o}/vs/${c.id}`)
  .join("\n")}

Full matrix: ${o}/compare

## More

- Full description for LLMs: ${o}/llms-full.txt
- Skills (JSON): ${o}/.well-known/agent-skills/index.json
- Human docs: ${site.docsUrl}
- Source: ${site.githubUrl}
`;
}
