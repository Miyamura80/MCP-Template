import type { APIRoute } from "astro";
import { site } from "../../config/landing";

/**
 * Well-known MCP discovery document. Lets clients locate the Model Context
 * Protocol server associated with this domain without prior configuration.
 */
export const GET: APIRoute = () => {
  const discovery = {
    schema_version: "2025-06-18",
    name: site.name,
    description: site.description,
    documentation: site.docsUrl,
    servers: [
      {
        name: site.serverName,
        url: site.mcpUrl,
        transport: "streamable-http",
      },
    ],
  };
  return new Response(JSON.stringify(discovery, null, 2), {
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
};
