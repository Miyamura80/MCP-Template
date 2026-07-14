import type { Draft, Thread } from "./types";

export function extractDraft(raw: unknown): Draft | null {
  if (!raw || typeof raw !== "object") return null;
  const wrapper = raw as { structuredContent?: unknown };
  const data = (wrapper.structuredContent ?? raw) as Record<string, unknown>;
  if (typeof data !== "object" || data === null) return null;
  const draftId = data["draft_id"];
  if (typeof draftId !== "string") return null;
  return {
    draft_id: draftId,
    from: typeof data["from"] === "string" ? (data["from"] as string) : undefined,
    to: typeof data["to"] === "string" ? (data["to"] as string) : undefined,
    cc: typeof data["cc"] === "string" ? (data["cc"] as string) : undefined,
    bcc: typeof data["bcc"] === "string" ? (data["bcc"] as string) : undefined,
    subject:
      typeof data["subject"] === "string" ? (data["subject"] as string) : undefined,
    body: typeof data["body"] === "string" ? (data["body"] as string) : undefined,
    thread_id:
      typeof data["thread_id"] === "string"
        ? (data["thread_id"] as string)
        : undefined,
  };
}

export function fieldsEqual(a: Draft, b: Draft): boolean {
  return (
    a.to === b.to &&
    a.cc === b.cc &&
    a.bcc === b.bcc &&
    a.subject === b.subject &&
    a.body === b.body
  );
}

export function extractThread(raw: unknown): Thread | null {
  if (!raw || typeof raw !== "object") return null;
  const wrapper = raw as { structuredContent?: unknown };
  const data = (wrapper.structuredContent ?? raw) as Record<string, unknown>;
  if (!data || typeof data !== "object") return null;
  if (!Array.isArray((data as { messages?: unknown }).messages)) return null;
  return data as unknown as Thread;
}

// Canonical enumeration of the user-editable draft fields, normalized to the
// empty-string wire form the server tools expect. persistDraft, onSend, and
// sentContextText must all agree on this set - a new Draft field is added
// here, not at each call site.
export function draftFields(draft: Draft): {
  to: string;
  cc: string;
  bcc: string;
  subject: string;
  body: string;
} {
  return {
    to: draft.to ?? "",
    cc: draft.cc ?? "",
    bcc: draft.bcc ?? "",
    subject: draft.subject ?? "",
    body: draft.body ?? "",
  };
}
