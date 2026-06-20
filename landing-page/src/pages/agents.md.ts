import type { APIRoute } from "astro";
import { agentsMarkdown } from "../utils/agent-content";

export const GET: APIRoute = () =>
  new Response(agentsMarkdown(), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
