import { getMcpEndpoint, getSiteOrigin } from "@/lib/site";

export const revalidate = false;

/**
 * Well-known MCP discovery document. Lets clients locate the Model Context
 * Protocol server associated with this domain without prior configuration.
 */
export async function GET() {
  const origin = await getSiteOrigin();
  const mcp = await getMcpEndpoint();

  const discovery = {
    schema_version: "2025-06-18",
    name: "MCP Template",
    description:
      "Streamable-HTTP MCP server exposing the shared service registry as tools.",
    documentation: `${origin}/docs/mcp`,
    servers: [
      {
        name: "mcp-template",
        url: mcp,
        transport: "streamable-http",
      },
    ],
  };

  return new Response(JSON.stringify(discovery, null, 2), {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "public, max-age=3600, s-maxage=3600",
    },
  });
}
