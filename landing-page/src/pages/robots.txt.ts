import type { APIRoute } from "astro";
import { AI_USER_AGENTS, abs } from "../utils/agent-content";

export const GET: APIRoute = () => {
  const lines: string[] = [
    "# Default policy: everything is indexable.",
    "User-agent: *",
    "Allow: /",
    "",
    "# AI / LLM crawlers and agents are explicitly welcome to read and index",
    "# this site. See /llms-full.txt and /agents.md.",
  ];

  for (const ua of AI_USER_AGENTS) {
    lines.push("", `User-agent: ${ua}`, "Allow: /");
  }

  lines.push(
    "",
    `Sitemap: ${abs("/sitemap.xml")}`,
    "",
    "# NLWeb / Schema Map structured-data feed.",
    "# https://schemamap.io  ·  https://github.com/nlweb-ai/NLWeb",
    `Schemamap: ${abs("/schemamap.xml")}`,
    "",
  );

  return new Response(lines.join("\n"), {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
};
