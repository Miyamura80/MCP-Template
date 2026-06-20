import { getMcpEndpoint, getSiteOrigin } from "@/lib/site";

export const revalidate = false;

/**
 * Agent-skills discovery endpoint. A machine-readable manifest describing the
 * skills/capabilities an agent can use here and the endpoints that back them.
 */
export async function GET() {
  const origin = await getSiteOrigin();
  const mcp = await getMcpEndpoint();

  const manifest = {
    name: "MCP Template",
    description:
      "Python template exposing one shared service registry over CLI, MCP, and HTTP API interfaces.",
    documentation: `${origin}/docs`,
    llms_full_txt: `${origin}/llms-full.txt`,
    llms_txt: `${origin}/llms.txt`,
    mcp: {
      endpoint: mcp,
      transport: "streamable-http",
      discovery: `${origin}/.well-known/mcp`,
    },
    skills: [
      {
        name: "browse-docs",
        description:
          "Read CLI, MCP, and HTTP API documentation as HTML or raw Markdown (append .mdx to any docs URL).",
        url: `${origin}/docs`,
      },
      {
        name: "call-mcp-tools",
        description:
          "Connect an MCP client over streamable HTTP to invoke the registered tools.",
        url: mcp,
      },
      {
        name: "ingest-corpus",
        description:
          "Fetch the entire documentation corpus as a single text file for LLM ingestion.",
        url: `${origin}/llms-full.txt`,
      },
    ],
  };

  return new Response(JSON.stringify(manifest, null, 2), {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "public, max-age=3600, s-maxage=3600",
    },
  });
}
