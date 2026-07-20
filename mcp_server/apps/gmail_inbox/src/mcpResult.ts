/**
 * Unwrap a CallToolResult from `app.callServerTool` into its typed payload.
 *
 * Prefers `structuredContent`; falls back to parsing JSON out of text
 * content blocks for hosts that don't forward structured output.
 */
export function extractStructuredContent<T>(raw: unknown): T | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;
  if (obj.structuredContent && typeof obj.structuredContent === "object") {
    return obj.structuredContent as T;
  }
  if (Array.isArray(obj.content)) {
    for (const item of obj.content) {
      if (item && typeof item === "object" && "text" in (item as Record<string, unknown>)) {
        try {
          const parsed = JSON.parse((item as { text: string }).text);
          if (parsed && typeof parsed === "object") return parsed as T;
        } catch { /* not JSON text content */ }
      }
    }
  }
  return null;
}
