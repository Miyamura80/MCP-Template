import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { sanitizeHtml } from "./sanitize";

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
type NewUpload = { filename: string; mime_type: string; data_base64: string };

// A file the user just dropped/selected, tracked while it is read + uploaded.
// Persisted attachments (with a real attachment_id) live on `draft.attachments`;
// these transient entries disappear once the save_draft response echoes them
// back as real attachments, or stick around with an error the user can dismiss.
type PendingUpload = {
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

type ComposerProps = {
  mcpApp: McpAppLike;
};

type SaveStatus =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; at: Date }
  | { kind: "error"; message: string };

type SentState = { message_id: string };

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
function extractAttachments(raw: unknown): DraftAttachment[] {
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
function readFileAsBase64(file: File): Promise<string> {
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
function formatBytes(n: number | undefined): string {
  if (typeof n !== "number" || n <= 0) return "";
  if (n < 1000) return `${n} B`;
  if (n < 1_000_000) return `${Math.round(n / 1000)} KB`;
  return `${(n / 1_000_000).toFixed(1)} MB`;
}

function fieldsEqual(a: Draft, b: Draft): boolean {
  return (
    a.to === b.to &&
    a.cc === b.cc &&
    a.bcc === b.bcc &&
    a.subject === b.subject &&
    a.body === b.body
  );
}

// Tracks whether the app is rendered in a narrow (mobile) viewport. Guards
// against `matchMedia` being unavailable (jsdom in tests) by falling back to
// desktop behavior, so the thread stays expanded and the body keeps its inner
// scroll there - exactly the "desktop is fine" case.
function useIsMobile(query = "(max-width: 600px)"): boolean {
  const getMatch = () =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(query).matches
      : false;
  const [matches, setMatches] = useState(getMatch);
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange();
    // Safari < 14 (older iOS) has no addEventListener on MediaQueryList and
    // only implements the legacy addListener/removeListener API. Calling the
    // missing method would throw at mount, so fall back when it's absent.
    if (typeof mql.addEventListener === "function") {
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    }
    mql.addListener(onChange);
    return () => mql.removeListener(onChange);
  }, [query]);
  return matches;
}

// Grows a textarea to fit its content while `enabled`, so there is no inner
// scroll region for a touch drag to fight. Recomputes on three triggers:
//   - `value` changes (typing / an agent rewriting the body),
//   - `enabled` flips (crossing the mobile breakpoint),
//   - the element's own width changes (host iframe resized *without* crossing
//     the breakpoint - otherwise the height would go stale and, because the
//     mobile style hides overflow, the extra lines would be unreachable).
// Uses useLayoutEffect so the box is sized before paint, avoiding a one-frame
// clip on each keystroke. When disabled it clears the inline height so the
// CSS fixed-height (desktop) box takes back over.
function useAutoGrow(
  ref: React.RefObject<HTMLTextAreaElement | null>,
  value: string,
  enabled: boolean,
) {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (!enabled) {
      el.style.height = "";
      return;
    }
    const resize = () => {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    };
    resize();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, value, enabled]);
}

export function Composer({ mcpApp }: ComposerProps) {
  const isMobile = useIsMobile();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [showCcBcc, setShowCcBcc] = useState(false);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>({ kind: "idle" });
  const [sent, setSent] = useState<SentState | null>(null);
  const [discarded, setDiscarded] = useState(false);
  const [confirmingDiscard, setConfirmingDiscard] = useState(false);
  const [pendingAgent, setPendingAgent] = useState<Draft | null>(null);
  const [uploads, setUploads] = useState<PendingUpload[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const localDirtyRef = useRef(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const draftRef = useRef<Draft | null>(null);
  const bodyRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const uploadSeqRef = useRef(0);
  const nextUploadId = () => `u${(uploadSeqRef.current += 1)}`;
  // The last server-confirmed attachment set - the authoritative keep-list a
  // whole-set-replace save must echo back. Advanced only by server responses
  // (never by optimistic UI), so concurrent mutations build on committed state.
  const attachmentsRef = useRef<DraftAttachment[]>([]);
  // Serializes every draft write (text autosave, attachment add/remove, send)
  // into one chain, so a whole-set-replace save can't interleave with another
  // and drop a file, and a slow save can't land after a newer one.
  const saveChainRef = useRef<Promise<unknown>>(Promise.resolve());
  const enqueue = <T,>(fn: () => Promise<T>): Promise<T> => {
    const run = saveChainRef.current.then(fn, fn);
    saveChainRef.current = run.then(
      () => undefined,
      () => undefined,
    );
    return run;
  };

  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  // On mobile the body grows to fit its content so there is no inner scroll
  // region competing with the page scroll - the whole iframe scrolls naturally
  // under a finger drag. Desktop keeps its fixed height and own scrollbar.
  useAutoGrow(bodyRef, draft?.body ?? "", isMobile);

  useEffect(() => {
    const handler = (raw: unknown) => {
      const incoming = extractDraft(raw);
      if (!incoming) return;
      const current = draftRef.current;
      if (!current) {
        setDraft(incoming);
        attachmentsRef.current = incoming.attachments ?? [];
        localDirtyRef.current = false;
        return;
      }
      if (localDirtyRef.current && !fieldsEqual(current, incoming)) {
        setPendingAgent(incoming);
        return;
      }
      setDraft(incoming);
      attachmentsRef.current = incoming.attachments ?? [];
      localDirtyRef.current = false;
    };
    mcpApp.ontoolresult = handler;
    return () => {
      if (mcpApp.ontoolresult === handler) {
        mcpApp.ontoolresult = undefined;
      }
    };
  }, [mcpApp]);

  // The single draft-write path. Reads the freshest text off draftRef at the
  // moment the call is issued (never a pre-await snapshot), so a save that fires
  // after a slow file read can't revert a concurrent edit. `attachments`
  // undefined => omit the arg entirely (server preserves existing files); an
  // array => whole-set replace. On success, when an attachment set was sent, the
  // server's echoed list becomes the authoritative truth. Throws on failure.
  const doSave = async (
    attachments?: (
      | { attachment_id: string }
      | { filename: string; mime_type: string; data_base64: string }
    )[],
  ): Promise<Draft | null> => {
    const snapshot = draftRef.current;
    if (!snapshot) return null;
    setSaveStatus({ kind: "saving" });
    const args: Record<string, unknown> = {
      draft_id: snapshot.draft_id,
      to: snapshot.to ?? "",
      cc: snapshot.cc ?? "",
      bcc: snapshot.bcc ?? "",
      subject: snapshot.subject ?? "",
      body: snapshot.body ?? "",
    };
    if (attachments !== undefined) args.attachments = attachments;
    try {
      const raw = await mcpApp.callServerTool({
        name: "gmail_composer.save_draft",
        arguments: args,
      });
      const saved = extractDraft(raw);
      if (attachments !== undefined) {
        // Advance the authoritative set synchronously; the caller commits the
        // matching React state (draft.attachments + upload chips) together so
        // there is no frame where a file shows as both a chip and a saved row.
        attachmentsRef.current = saved?.attachments ?? [];
      }
      setSaveStatus({ kind: "saved", at: new Date() });
      // Clear dirty only if nothing newer was typed than the text we just sent.
      const latest = draftRef.current;
      if (latest && fieldsEqual(latest, snapshot)) {
        localDirtyRef.current = false;
      }
      return saved;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setSaveStatus({ kind: "error", message: msg });
      throw err;
    }
  };

  const scheduleAutoSave = () => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      // Text-only save: omit attachments so existing files are preserved.
      enqueue(() => doSave()).catch(() => {});
    }, 800);
  };

  const updateField = (key: keyof Draft, value: string) => {
    if (!draft) return;
    const next: Draft = { ...draft, [key]: value };
    setDraft(next);
    localDirtyRef.current = true;
    scheduleAutoSave();
  };

  const onSaveNow = () => {
    if (!draftRef.current) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    enqueue(() => doSave()).catch(() => {});
  };

  const onSend = async () => {
    if (!draftRef.current) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    try {
      // Serialized after any in-flight attachment upload so the files are
      // persisted before we send; reads the freshest text at issue time.
      const raw = await enqueue(() => {
        const cur = draftRef.current;
        if (!cur) throw new Error("no draft to send");
        return mcpApp.callServerTool({
          name: "gmail_composer.send",
          arguments: {
            draft_id: cur.draft_id,
            to: cur.to ?? "",
            cc: cur.cc ?? "",
            bcc: cur.bcc ?? "",
            subject: cur.subject ?? "",
            body: cur.body ?? "",
          },
        });
      });
      const wrapper = (raw ?? {}) as { structuredContent?: { message_id?: string } };
      const inner = wrapper.structuredContent ?? (raw as { message_id?: string });
      const messageId = (inner as { message_id?: string })?.message_id ?? "";
      setSent({ message_id: messageId });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setSaveStatus({ kind: "error", message: msg });
    }
  };

  const onDiscardConfirm = async () => {
    if (!draft) return;
    // Cancel any queued autosave so a debounced save can't race ahead and
    // re-create a row immediately after the discard call returns.
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    try {
      await mcpApp.callServerTool({
        name: "gmail_composer.discard",
        arguments: { draft_id: draft.draft_id },
      });
      setDiscarded(true);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setSaveStatus({ kind: "error", message: msg });
    } finally {
      setConfirmingDiscard(false);
    }
  };

  const applyAgentUpdate = () => {
    if (!pendingAgent) return;
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    setDraft(pendingAgent);
    attachmentsRef.current = pendingAgent.attachments ?? [];
    setPendingAgent(null);
    localDirtyRef.current = false;
  };

  const keepLocal = () => setPendingAgent(null);

  const onFilesChosen = async (files: FileList | File[] | null) => {
    if (!draftRef.current || !files) return;
    const chosen = Array.from(files);
    if (chosen.length === 0) return;

    // Oversized files never leave the browser: mark them errored inline so the
    // user sees why, and don't include them in the upload call.
    const oversized = chosen.filter((f) => f.size > MAX_ATTACHMENT_BYTES);
    const okFiles = chosen.filter((f) => f.size <= MAX_ATTACHMENT_BYTES);

    const okEntries: PendingUpload[] = okFiles.map((f) => ({
      local_id: nextUploadId(),
      filename: f.name,
      mime_type: f.type || "application/octet-stream",
      size: f.size,
      status: "reading",
    }));
    const oversizedEntries: PendingUpload[] = oversized.map((f) => ({
      local_id: nextUploadId(),
      filename: f.name,
      mime_type: f.type || "application/octet-stream",
      size: f.size,
      status: "error",
      error: `Too large (${formatBytes(f.size)}); ${formatBytes(MAX_ATTACHMENT_BYTES)} max`,
    }));
    setUploads((u) => [...u, ...okEntries, ...oversizedEntries]);
    if (okFiles.length === 0) return;

    // Read each file independently: one unreadable file must not sink the whole
    // batch, so failures are marked per-chip and the rest still upload.
    const results = await Promise.allSettled(okFiles.map(readFileAsBase64));
    const ready: { id: string; upload: NewUpload }[] = [];
    const failedIds = new Set<string>();
    results.forEach((r, i) => {
      const entry = okEntries[i];
      if (r.status === "fulfilled") {
        ready.push({
          id: entry.local_id,
          upload: {
            filename: entry.filename,
            mime_type: entry.mime_type,
            data_base64: r.value,
          },
        });
      } else {
        failedIds.add(entry.local_id);
      }
    });
    if (failedIds.size > 0) {
      setUploads((u) =>
        u.map((e) =>
          failedIds.has(e.local_id)
            ? { ...e, status: "error", error: "Could not read file" }
            : e,
        ),
      );
    }
    if (ready.length === 0) {
      setSaveStatus({ kind: "error", message: "Could not read the selected file(s)" });
      return;
    }
    const readyIds = new Set(ready.map((r) => r.id));
    setUploads((u) =>
      u.map((e) => (readyIds.has(e.local_id) ? { ...e, status: "uploading" } : e)),
    );
    try {
      const saved = await enqueue(() => {
        // Build the keep-set from the authoritative server-confirmed list at run
        // time (prior queued ops have settled), so overlapping drops that each
        // started from the same stale draft can't drop one another's files.
        const keep = attachmentsRef.current.map((a) => ({
          attachment_id: a.attachment_id,
        }));
        return doSave([...keep, ...ready.map((r) => r.upload)]);
      });
      // Commit the adopted list and drop the transient chips in one batch, so
      // the just-uploaded file never renders as both a chip and a saved row.
      const next = saved?.attachments ?? attachmentsRef.current;
      setDraft((d) => (d ? { ...d, attachments: next } : d));
      setUploads((u) => u.filter((e) => !readyIds.has(e.local_id)));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setUploads((u) =>
        u.map((e) =>
          readyIds.has(e.local_id) ? { ...e, status: "error", error: msg } : e,
        ),
      );
    }
  };

  const onRemoveAttachment = async (attachmentId: string) => {
    if (!draftRef.current) return;
    // Optimistic UI only. The authoritative keep-set is recomputed inside the
    // queued op from attachmentsRef (advanced by server responses, not by this
    // optimistic edit), so a remove that overlaps an upload can't clobber it.
    setDraft((d) =>
      d
        ? { ...d, attachments: (d.attachments ?? []).filter((a) => a.attachment_id !== attachmentId) }
        : d,
    );
    try {
      const saved = await enqueue(() => {
        const keep = attachmentsRef.current
          .filter((a) => a.attachment_id !== attachmentId)
          .map((a) => ({ attachment_id: a.attachment_id }));
        return doSave(keep);
      });
      // Reconcile the optimistic UI with the server's confirmed list.
      const next = saved?.attachments ?? attachmentsRef.current;
      setDraft((d) => (d ? { ...d, attachments: next } : d));
    } catch {
      // doSave only advances attachmentsRef on success, so it still holds the
      // pre-remove set: revert the optimistic UI to it.
      setDraft((d) => (d ? { ...d, attachments: attachmentsRef.current } : d));
    }
  };

  const dismissUpload = (localId: string) =>
    setUploads((u) => u.filter((e) => e.local_id !== localId));

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    void onFilesChosen(e.dataTransfer?.files ?? null);
  };

  // Thread context: fetch when draft has a thread_id
  const [thread, setThread] = useState<Thread | null>(null);
  // `null` means "follow the viewport default": collapsed on mobile so the
  // reply box is reachable without scrolling past a long thread, expanded on
  // desktop. Once the user toggles, their explicit choice sticks.
  const [threadCollapsed, setThreadCollapsed] = useState<boolean | null>(null);
  const threadCollapsedEffective = threadCollapsed ?? isMobile;
  const fetchedThreadRef = useRef<string | null>(null);

  useEffect(() => {
    if (!draft?.thread_id) {
      setThread(null);
      fetchedThreadRef.current = null;
      return;
    }
    if (draft.thread_id === fetchedThreadRef.current) return;
    const tid = draft.thread_id;
    setThread(null);
    let cancelled = false;
    mcpApp
      .callServerTool({
        name: "gmail_composer.get_thread",
        arguments: { thread_id: tid },
      })
      .then((raw) => {
        if (cancelled) return;
        fetchedThreadRef.current = tid;
        const data = extractThread(raw);
        if (data) setThread(data);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [draft?.thread_id, mcpApp]);

  if (sent) {
    return (
      <div style={containerStyle}>
        <div style={successStyle}>Sent &#10003; - message_id: {sent.message_id}</div>
      </div>
    );
  }
  if (discarded) {
    return (
      <div style={containerStyle}>
        <div style={mutedStyle}>Discarded.</div>
      </div>
    );
  }
  if (!draft) {
    return <div style={containerStyle}>Waiting for draft…</div>;
  }

  return (
    <div style={containerStyle}>
      {thread && thread.messages.length > 0 && (
        <ThreadPanel
          thread={thread}
          collapsed={threadCollapsedEffective}
          messagesStyle={
            isMobile ? mobileThreadMessagesContainer : threadMessagesContainer
          }
          onToggle={() => setThreadCollapsed(!threadCollapsedEffective)}
        />
      )}

      <header style={headerStyle}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Draft</h2>
        <span style={statusStyle(saveStatus)} data-testid="save-status">
          {renderStatus(saveStatus)}
        </span>
      </header>

      {pendingAgent && (
        <div style={agentBannerStyle} role="status">
          <span>Agent updated this draft.</span>
          <button onClick={applyAgentUpdate} style={smallPrimaryStyle}>
            Apply
          </button>
          <button onClick={keepLocal} style={smallSecondaryStyle}>
            Keep mine
          </button>
        </div>
      )}

      <Row label="From">
        <div style={readOnlyStyle}>{draft.from ?? "(connected account)"}</div>
      </Row>

      <Row label="To">
        <input
          type="text"
          value={draft.to ?? ""}
          onChange={(e) => updateField("to", e.target.value)}
          style={inputStyle}
          aria-label="To"
        />
      </Row>

      {!showCcBcc ? (
        <div style={{ marginBottom: 8 }}>
          <button onClick={() => setShowCcBcc(true)} style={linkButtonStyle}>
            Show Cc/Bcc
          </button>
        </div>
      ) : (
        <>
          <Row label="Cc">
            <input
              type="text"
              value={draft.cc ?? ""}
              onChange={(e) => updateField("cc", e.target.value)}
              style={inputStyle}
              aria-label="Cc"
            />
          </Row>
          <Row label="Bcc">
            <input
              type="text"
              value={draft.bcc ?? ""}
              onChange={(e) => updateField("bcc", e.target.value)}
              style={inputStyle}
              aria-label="Bcc"
            />
          </Row>
        </>
      )}

      <Row label="Subject">
        <input
          type="text"
          value={draft.subject ?? ""}
          onChange={(e) => updateField("subject", e.target.value)}
          style={inputStyle}
          aria-label="Subject"
        />
      </Row>

      <textarea
        ref={bodyRef}
        value={draft.body ?? ""}
        onChange={(e) => updateField("body", e.target.value)}
        rows={14}
        style={isMobile ? mobileTextareaStyle : textareaStyle}
        aria-label="Body"
      />

      <div
        style={dragActive ? dropZoneActiveStyle : dropZoneStyle}
        onDragOver={(e) => {
          e.preventDefault();
          if (!dragActive) setDragActive(true);
        }}
        onDragEnter={(e) => {
          e.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={(e) => {
          // Only clear when the pointer actually leaves the zone, not when it
          // crosses onto a child element inside it.
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
            setDragActive(false);
          }
        }}
        onDrop={onDrop}
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple
          style={{ display: "none" }}
          aria-label="Attach files"
          onChange={(e) => {
            void onFilesChosen(e.target.files);
            // Reset so choosing the same file twice fires change again.
            e.target.value = "";
          }}
        />
        <div style={dropHintStyle}>
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            style={secondaryButtonStyle}
          >
            Attach files
          </button>
          <span style={{ color: "#888", fontSize: 12 }}>
            or drag &amp; drop &middot; up to 25 MB total (Gmail&apos;s limit)
          </span>
        </div>

        {((draft.attachments?.length ?? 0) > 0 || uploads.length > 0) && (
          <ul style={attachmentListStyle} aria-label="Attachments">
            {(draft.attachments ?? []).map((a) => (
              <li key={a.attachment_id} style={attachmentItemStyle}>
                <span style={attachmentIconStyle}>📎</span>
                <span style={attachmentNameStyle} title={a.filename}>
                  {a.filename}
                </span>
                {a.size ? (
                  <span style={attachmentMetaStyle}>{formatBytes(a.size)}</span>
                ) : null}
                <button
                  type="button"
                  onClick={() => void onRemoveAttachment(a.attachment_id)}
                  style={attachmentRemoveStyle}
                  aria-label={`Remove ${a.filename}`}
                >
                  ✕
                </button>
              </li>
            ))}
            {uploads.map((u) => (
              <li
                key={u.local_id}
                style={{
                  ...attachmentItemStyle,
                  ...(u.status === "error" ? attachmentErrorItemStyle : {}),
                }}
              >
                <span style={attachmentIconStyle}>
                  {u.status === "error" ? "⚠️" : "⏳"}
                </span>
                <span style={attachmentNameStyle} title={u.filename}>
                  {u.filename}
                </span>
                <span style={attachmentMetaStyle}>
                  {u.status === "error"
                    ? (u.error ?? "Failed")
                    : u.status === "reading"
                      ? "Reading…"
                      : "Uploading…"}
                </span>
                {u.status === "error" && (
                  <button
                    type="button"
                    onClick={() => dismissUpload(u.local_id)}
                    style={attachmentRemoveStyle}
                    aria-label={`Dismiss ${u.filename}`}
                  >
                    ✕
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div style={buttonRowStyle}>
        <button onClick={onSend} style={primaryButtonStyle}>
          Send
        </button>
        <button onClick={onSaveNow} style={secondaryButtonStyle}>
          Save draft
        </button>
        {!confirmingDiscard ? (
          <button
            onClick={() => setConfirmingDiscard(true)}
            style={destructiveButtonStyle}
          >
            Discard
          </button>
        ) : (
          <span style={confirmRowStyle}>
            Discard?
            <button onClick={onDiscardConfirm} style={destructiveButtonStyle}>
              Yes, discard
            </button>
            <button
              onClick={() => setConfirmingDiscard(false)}
              style={secondaryButtonStyle}
            >
              Cancel
            </button>
          </span>
        )}
      </div>
    </div>
  );
}

function extractThread(raw: unknown): Thread | null {
  if (!raw || typeof raw !== "object") return null;
  const wrapper = raw as { structuredContent?: unknown };
  const data = (wrapper.structuredContent ?? raw) as Record<string, unknown>;
  if (!data || typeof data !== "object") return null;
  if (!Array.isArray((data as { messages?: unknown }).messages)) return null;
  return data as unknown as Thread;
}

function ThreadPanel({
  thread,
  collapsed,
  messagesStyle,
  onToggle,
}: {
  thread: Thread;
  collapsed: boolean;
  messagesStyle: React.CSSProperties;
  onToggle: () => void;
}) {
  return (
    <div style={threadPanelStyle}>
      <button onClick={onToggle} style={threadToggleBtn}>
        {collapsed ? "▶" : "▼"} Conversation ({thread.messages.length} message
        {thread.messages.length === 1 ? "" : "s"})
      </button>
      {!collapsed && (
        <div style={messagesStyle}>
          {thread.messages.map((m, i) => (
            <ThreadMessageView
              key={m.message_id}
              message={m}
              defaultExpanded={i === thread.messages.length - 1}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ThreadMessageView({
  message,
  defaultExpanded,
}: {
  message: ThreadMessage;
  defaultExpanded: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  if (!expanded) {
    return (
      <div
        style={threadMsgCollapsedStyle}
        onClick={() => setExpanded(true)}
      >
        <strong style={{ fontSize: 12 }}>{message.from || "(unknown)"}</strong>
        <span style={{ color: "#888", fontSize: 11, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {message.body_text?.slice(0, 100) || message.subject || ""}
        </span>
        <span style={{ color: "#999", fontSize: 10, flexShrink: 0 }}>
          {relativeTime(message.date)}
        </span>
      </div>
    );
  }

  return (
    <div style={threadMsgExpandedStyle}>
      <div
        style={{ display: "flex", justifyContent: "space-between", marginBottom: 6, cursor: "pointer" }}
        onClick={() => setExpanded(false)}
      >
        <div>
          <strong style={{ fontSize: 12 }}>{message.from || "(unknown)"}</strong>
          {message.to && <span style={{ fontSize: 11, color: "#666", marginLeft: 8 }}>to {message.to}</span>}
        </div>
        <span style={{ fontSize: 10, color: "#999" }}>{relativeTime(message.date)}</span>
      </div>
      <ThreadMessageBody message={message} />
    </div>
  );
}

function ThreadMessageBody({ message }: { message: ThreadMessage }) {
  const [showQuoted, setShowQuoted] = useState(false);

  if (message.body_html) {
    const { main, quoted } = splitHtmlAtQuote(message.body_html);
    return (
      <div>
        <div style={threadBodyHtmlStyle} dangerouslySetInnerHTML={{ __html: sanitizeHtml(main) }} />
        {quoted && (
          <>
            <button onClick={() => setShowQuoted((v) => !v)} style={quoteToggleBtnStyle}>
              &bull;&bull;&bull;
            </button>
            {showQuoted && (
              <div
                style={{ ...threadBodyHtmlStyle, borderLeft: "3px solid #dadce0", paddingLeft: 8, marginTop: 4 }}
                dangerouslySetInnerHTML={{ __html: sanitizeHtml(quoted) }}
              />
            )}
          </>
        )}
      </div>
    );
  }
  if (message.body_text) {
    const { main, quoted } = splitTextAtQuote(message.body_text);
    return (
      <div>
        <pre style={threadBodyTextStyle}>{main}</pre>
        {quoted && (
          <>
            <button onClick={() => setShowQuoted((v) => !v)} style={quoteToggleBtnStyle}>
              &bull;&bull;&bull;
            </button>
            {showQuoted && (
              <pre style={{ ...threadBodyTextStyle, borderLeft: "3px solid #dadce0", paddingLeft: 8, marginTop: 4 }}>{quoted}</pre>
            )}
          </>
        )}
      </div>
    );
  }
  return <div style={{ color: "#888", fontSize: 12 }}>(no body)</div>;
}

function splitHtmlAtQuote(html: string): { main: string; quoted: string | null } {
  const markers = [
    '<div class="gmail_quote"',
    "<div class=\"gmail_quote\"",
    '<blockquote class="gmail_quote"',
    "<blockquote class=\"gmail_quote\"",
    '<div class=3D"gmail_quote"',
  ];
  for (const marker of markers) {
    const idx = html.indexOf(marker);
    if (idx > 0) return { main: html.slice(0, idx), quoted: html.slice(idx) };
  }
  const onWroteRe = /(<br\s*\/?>[\s\S]{0,20}?On\s.{10,80}\s+wrote:\s*<br\s*\/?>)/i;
  const m = onWroteRe.exec(html);
  if (m && m.index > 50) return { main: html.slice(0, m.index), quoted: html.slice(m.index) };
  return { main: html, quoted: null };
}

function splitTextAtQuote(text: string): { main: string; quoted: string | null } {
  const lines = text.split("\n");
  const onWroteRe = /^On .{10,80} wrote:\s*$/;
  for (let i = 0; i < lines.length; i++) {
    if (onWroteRe.test(lines[i]) && i > 0) {
      return { main: lines.slice(0, i).join("\n"), quoted: lines.slice(i).join("\n") };
    }
  }
  let firstQuoteLine = -1;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].startsWith(">")) {
      if (firstQuoteLine === -1) firstQuoteLine = i;
    } else if (firstQuoteLine !== -1) {
      break;
    }
  }
  if (firstQuoteLine > 0 && lines.length - firstQuoteLine >= 3) {
    return { main: lines.slice(0, firstQuoteLine).join("\n"), quoted: lines.slice(firstQuoteLine).join("\n") };
  }
  return { main: text, quoted: null };
}

function relativeTime(iso: string | undefined): string {
  if (!iso) return "";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return "";
  const ageMs = Date.now() - dt.getTime();
  const mins = Math.round(ageMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return dt.toLocaleDateString();
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={rowStyle}>
      <div style={labelStyle}>{label}</div>
      <div style={{ flex: 1 }}>{children}</div>
    </div>
  );
}

function renderStatus(s: SaveStatus): string {
  switch (s.kind) {
    case "idle":
      return "";
    case "saving":
      return "Saving…";
    case "saved":
      return `Saved at ${s.at.getHours().toString().padStart(2, "0")}:${s.at
        .getMinutes()
        .toString()
        .padStart(2, "0")}`;
    case "error":
      return `Save failed: ${s.message}`;
  }
}

const containerStyle: React.CSSProperties = {
  fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif",
  padding: 16,
  maxWidth: 720,
  color: "#111",
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  marginBottom: 12,
};

const rowStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  alignItems: "center",
  marginBottom: 8,
};

const labelStyle: React.CSSProperties = {
  width: 70,
  color: "#555",
  fontSize: 13,
};

const inputStyle: React.CSSProperties = {
  flex: 1,
  padding: "6px 8px",
  border: "1px solid #ddd",
  borderRadius: 4,
  fontSize: 14,
  width: "100%",
  boxSizing: "border-box",
};

const readOnlyStyle: React.CSSProperties = {
  padding: "6px 8px",
  color: "#666",
  fontSize: 14,
};

const textareaStyle: React.CSSProperties = {
  width: "100%",
  padding: 8,
  border: "1px solid #ddd",
  borderRadius: 4,
  fontSize: 14,
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
  marginTop: 8,
  boxSizing: "border-box",
};

// Mobile: the box auto-grows to its content (see the effect in Composer), so
// there is no inner scroll to trap a finger drag - the page scrolls instead.
// `touchAction: manipulation` and a comfortable min-height round it out.
const mobileTextareaStyle: React.CSSProperties = {
  ...textareaStyle,
  minHeight: 180,
  overflowY: "hidden",
  resize: "none",
  fontSize: 16, // prevents iOS Safari from zooming in on focus
  touchAction: "manipulation",
};

const dropZoneStyle: React.CSSProperties = {
  marginTop: 10,
  padding: 10,
  border: "1px dashed #d1d5db",
  borderRadius: 6,
  background: "#fafafa",
  transition: "background 0.12s, border-color 0.12s",
};

const dropZoneActiveStyle: React.CSSProperties = {
  ...dropZoneStyle,
  borderColor: "#3b82f6",
  background: "#eff6ff",
};

const dropHintStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  alignItems: "center",
  flexWrap: "wrap",
};

const attachmentListStyle: React.CSSProperties = {
  listStyle: "none",
  margin: "10px 0 0",
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const attachmentItemStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "4px 8px",
  borderRadius: 4,
  background: "#fff",
  border: "1px solid #eee",
  fontSize: 13,
};

const attachmentErrorItemStyle: React.CSSProperties = {
  background: "#fef2f2",
  border: "1px solid #fecaca",
};

const attachmentIconStyle: React.CSSProperties = {
  flexShrink: 0,
  fontSize: 13,
};

const attachmentNameStyle: React.CSSProperties = {
  flex: 1,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  color: "#111",
};

const attachmentMetaStyle: React.CSSProperties = {
  flexShrink: 0,
  color: "#888",
  fontSize: 12,
};

const attachmentRemoveStyle: React.CSSProperties = {
  flexShrink: 0,
  background: "transparent",
  border: "none",
  color: "#991b1b",
  cursor: "pointer",
  fontSize: 13,
  lineHeight: 1,
  padding: 2,
};

const buttonRowStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  marginTop: 12,
  alignItems: "center",
  flexWrap: "wrap",
};

const primaryButtonStyle: React.CSSProperties = {
  background: "#3b82f6",
  color: "white",
  border: "none",
  padding: "6px 14px",
  borderRadius: 6,
  cursor: "pointer",
};

const secondaryButtonStyle: React.CSSProperties = {
  background: "#f3f4f6",
  color: "#111",
  border: "1px solid #ddd",
  padding: "6px 12px",
  borderRadius: 6,
  cursor: "pointer",
};

const destructiveButtonStyle: React.CSSProperties = {
  background: "transparent",
  color: "#991b1b",
  border: "1px solid #fecaca",
  padding: "6px 12px",
  borderRadius: 6,
  cursor: "pointer",
};

const linkButtonStyle: React.CSSProperties = {
  background: "transparent",
  border: "none",
  color: "#3b82f6",
  padding: 0,
  cursor: "pointer",
  fontSize: 13,
};

const smallPrimaryStyle: React.CSSProperties = {
  ...primaryButtonStyle,
  padding: "2px 8px",
  fontSize: 12,
};

const smallSecondaryStyle: React.CSSProperties = {
  ...secondaryButtonStyle,
  padding: "2px 8px",
  fontSize: 12,
};

const confirmRowStyle: React.CSSProperties = {
  display: "inline-flex",
  gap: 8,
  alignItems: "center",
  fontSize: 13,
  color: "#991b1b",
};

const agentBannerStyle: React.CSSProperties = {
  background: "#fff7ed",
  border: "1px solid #fed7aa",
  padding: "6px 10px",
  borderRadius: 6,
  marginBottom: 8,
  display: "flex",
  gap: 8,
  alignItems: "center",
  fontSize: 13,
};

const successStyle: React.CSSProperties = {
  background: "#ecfdf5",
  border: "1px solid #a7f3d0",
  padding: "10px 12px",
  borderRadius: 6,
  color: "#065f46",
};

const mutedStyle: React.CSSProperties = {
  color: "#666",
};

const threadPanelStyle: React.CSSProperties = {
  borderBottom: "1px solid #e5e7eb",
  marginBottom: 12,
  paddingBottom: 8,
};

const threadToggleBtn: React.CSSProperties = {
  background: "none",
  border: "none",
  padding: "4px 0",
  cursor: "pointer",
  fontSize: 13,
  color: "#374151",
  fontWeight: 600,
};

const threadMessagesContainer: React.CSSProperties = {
  maxHeight: 280,
  overflowY: "auto",
  WebkitOverflowScrolling: "touch",
  marginTop: 6,
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

// Mobile: drop the fixed-height inner scroll region. Nested scrolling inside
// the iframe is the thing that feels broken under touch, so let the messages
// flow and the whole page scroll instead.
const mobileThreadMessagesContainer: React.CSSProperties = {
  ...threadMessagesContainer,
  maxHeight: "none",
  overflowY: "visible",
};

const threadMsgCollapsedStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "6px 8px",
  borderRadius: 4,
  background: "#f9fafb",
  cursor: "pointer",
  border: "1px solid #f3f4f6",
};

const threadMsgExpandedStyle: React.CSSProperties = {
  padding: "8px 10px",
  borderRadius: 4,
  background: "#f9fafb",
  border: "1px solid #e5e7eb",
};

const threadBodyHtmlStyle: React.CSSProperties = {
  fontSize: 12,
  color: "#374151",
  lineHeight: 1.5,
  wordBreak: "break-word",
};

const threadBodyTextStyle: React.CSSProperties = {
  whiteSpace: "pre-wrap",
  fontFamily: "inherit",
  margin: 0,
  fontSize: 12,
  color: "#374151",
};

const quoteToggleBtnStyle: React.CSSProperties = {
  display: "block",
  background: "#f1f3f4",
  border: "none",
  borderRadius: 4,
  padding: "2px 10px",
  fontSize: 12,
  color: "#5f6368",
  cursor: "pointer",
  marginTop: 4,
  letterSpacing: 2,
  fontWeight: 700,
  lineHeight: 1,
};

function statusStyle(s: SaveStatus): React.CSSProperties {
  if (s.kind === "error") return { color: "#991b1b", fontSize: 12 };
  if (s.kind === "saved") return { color: "#059669", fontSize: 12 };
  return { color: "#666", fontSize: 12 };
}
