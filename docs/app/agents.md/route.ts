import { getMcpEndpoint, getSiteOrigin } from "@/lib/site";

export const revalidate = false;

/**
 * Agent discovery document (Markdown). A human- and agent-readable entry point
 * describing how an autonomous agent can interact with this project: where the
 * docs are, where the machine-readable corpus is, and how to reach the MCP
 * server.
 */
export async function GET() {
  const origin = await getSiteOrigin();
  const mcp = await getMcpEndpoint();

  const body = `# Agents

MCP Template is a Python project that exposes one shared service registry over
three interfaces: a CLI, an MCP server, and an HTTP API.

## How agents should use this site

- **Full corpus for LLMs:** ${origin}/llms-full.txt
- **Index for LLMs:** ${origin}/llms.txt
- **Sitemap:** ${origin}/sitemap.xml
- **Schema Map feed:** ${origin}/schemamap.xml
- **Raw Markdown:** append \`.mdx\` to any docs URL (e.g. ${origin}/docs.mdx)
- **Agent view:** append \`?mode=agent\` to the homepage for a structured,
  link-first summary.

## MCP server

The Model Context Protocol server is the primary programmatic surface.

- **Endpoint:** ${mcp} (streamable HTTP)
- **Discovery:** ${origin}/.well-known/mcp
- **Tools reference:** ${origin}/docs/mcp/tools
- **Setup guide:** ${origin}/docs/mcp/setup

## Capabilities

- Browse documentation for the CLI, MCP, and HTTP API interfaces.
- Connect an MCP client to call the registered tools.
- Read every page as plain Markdown or as a single concatenated text file.
`;

  return new Response(body, {
    headers: {
      "Content-Type": "text/markdown; charset=utf-8",
      "Cache-Control": "public, max-age=3600, s-maxage=3600",
    },
  });
}
