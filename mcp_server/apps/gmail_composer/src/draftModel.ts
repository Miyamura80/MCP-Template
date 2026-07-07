export type DraftAttachment = {
  attachment_id: string;
  filename: string;
  mime_type?: string;
  size?: number;
};

export type Draft = {
  draft_id: string;
  from?: string;
  to?: string;
  cc?: string;
  bcc?: string;
  subject?: string;
  body?: string;
  thread_id?: string;
  attachments?: DraftAttachment[];
};

// Cap a single attachment just under the server's limit: AttachmentInput caps
// base64 at 34M chars (~25.5 MB decoded), and Gmail's ceiling is 25 MB for the
// WHOLE message. 25 MB (decimal) keeps a per-file drop safely inside the base64
// validator so the client guard actually prevents a wasted round-trip. The
// total-message limit (many files summing past 25 MB) is still enforced
// server-side and surfaces as a save/send error.
export const MAX_ATTACHMENT_BYTES = 25_000_000;

// A ready-to-upload attachment: the server's AttachmentInput shape.
export type NewUpload = { filename: string; mime_type: string; data_base64: string };

// A file the user just dropped/selected, tracked while it is read + uploaded.
// Persisted attachments (with a real attachment_id) live on `draft.attachments`;
// these transient entries disappear once the save_draft response echoes them
// back as real attachments, or stick around with an error the user can dismiss.
export type PendingUpload = {
  local_id: string;
  filename: string;
  mime_type: string;
  size: number;
  status: "reading" | "uploading" | "error";
  error?: string;
};

export type ThreadMessage = {
  message_id: string;
  from?: string;
  to?: string;
  cc?: string;
  date?: string;
  subject?: string;
  body_text?: string;
  body_html?: string;
};

export type Thread = {
  thread_id: string;
  messages: ThreadMessage[];
};

export type McpAppLike = {
  ontoolresult?: (result: any) => void;  // eslint-disable-line @typescript-eslint/no-explicit-any
  callServerTool: (args: { name: string; arguments: Record<string, unknown> }) => Promise<unknown>;
};

export type SaveStatus =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; at: Date }
  | { kind: "error"; message: string };

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
    attachments: extractAttachments(data["attachments"]),
  };
}

// Pull the draft's existing attachments (each with a stable attachment_id) off a
// GmailDraft payload. Only files with an id are usable here - the id is what we
// pass back as a reference to preserve the file on the next save.
export function extractAttachments(raw: unknown): DraftAttachment[] {
  if (!Array.isArray(raw)) return [];
  const out: DraftAttachment[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const a = item as Record<string, unknown>;
    const id = a["attachment_id"];
    if (typeof id !== "string" || id.length === 0) continue;
    // GmailDraftAttachment emits both `size` and the computed `size_bytes`;
    // prefer the public `size_bytes` name and fall back to `size`.
    const rawSize = a["size_bytes"] ?? a["size"];
    out.push({
      attachment_id: id,
      filename: typeof a["filename"] === "string" ? (a["filename"] as string) : "(file)",
      mime_type: typeof a["mime_type"] === "string" ? (a["mime_type"] as string) : undefined,
      size: typeof rawSize === "number" ? rawSize : undefined,
    });
  }
  return out;
}

// Read a File to bare base64 (no data: URL prefix), the shape AttachmentInput
// wants. FileReader is available in every host iframe and in jsdom, so this
// path is identical in the app and in tests.
export function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () =>
      reject(reader.error ?? new Error(`Could not read ${file.name}`));
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.readAsDataURL(file);
  });
}

// Decimal (SI) units, matching how the 25 MB limit is expressed to the user so
// a file shown as "25.0 MB" is exactly at the cap, not off by the 1024 factor.
export function formatBytes(n: number | undefined): string {
  if (typeof n !== "number" || n <= 0) return "";
  if (n < 1000) return `${n} B`;
  if (n < 1_000_000) return `${Math.round(n / 1000)} KB`;
  return `${(n / 1_000_000).toFixed(1)} MB`;
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
