import type { APIRoute } from "astro";
import { abs } from "../../utils/agent-content";
import { site } from "../../config/landing";

/**
 * Agent-skills discovery endpoint: a machine-readable manifest describing the
 * capabilities an agent can use here and the endpoints that back them.
 */
export const GET: APIRoute = () => {
  const manifest = {
    name: site.name,
    description: site.description,
    documentation: site.docsUrl,
    llms_full_txt: abs("/llms-full.txt"),
    llms_txt: abs("/llms.txt"),
    mcp: {
      endpoint: site.mcpUrl,
      server_name: site.serverName,
      transport: "streamable-http",
      discovery: abs("/.well-known/mcp"),
    },
    skills: [
      {
        name: "connect-mcp",
        description:
          "Add the streamable-HTTP MCP endpoint to an MCP client to discover and call the server's tools with typed inputs.",
        url: site.mcpUrl,
      },
      {
        name: "read-corpus",
        description:
          "Fetch the full site corpus as a single text file for LLM ingestion.",
        url: abs("/llms-full.txt"),
      },
    ],
  };
  return new Response(JSON.stringify(manifest, null, 2), {
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
};
