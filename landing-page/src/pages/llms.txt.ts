import type { APIRoute } from "astro";
import { llmsIndexText } from "../utils/agent-content";

export const GET: APIRoute = () =>
  new Response(llmsIndexText(), {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
