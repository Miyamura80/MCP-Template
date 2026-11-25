// Unwrap an MCP CallToolResult to its structured payload: prefer
// `structuredContent`, else parse a JSON `TextContent` item. Returns null when
// neither is present (never the raw envelope), so a malformed result can't
// masquerade as app data and content-only JSON results aren't dropped.
export function extractStructuredContent<T>(raw: unknown): T | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;
  if (obj.structuredContent && typeof obj.structuredContent === "object") {
    return obj.structuredContent as T;
  }
  if (Array.isArray(obj.content)) {
    for (const item of obj.content) {
      if (!item || typeof item !== "object") continue;
      // Only a real MCP TextContent block qualifies: a `type: "text"`
      // discriminator with a string `text`. Without this an image, resource or
      // other block that happens to carry a `text` field could smuggle JSON in.
      const candidate = item as { type?: unknown; text?: unknown };
      if (candidate.type !== "text" || typeof candidate.text !== "string") continue;
      try {
        const parsed = JSON.parse(candidate.text);
        if (parsed && typeof parsed === "object") return parsed as T;
      } catch {
        /* not JSON text content */
      }
    }
  }
  return null;
}

export function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
