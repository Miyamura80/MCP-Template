import { useEffect, useRef, useState } from "react";
import {
  MAX_ATTACHMENT_BYTES,
  extractDraft,
  fieldsEqual,
  formatBytes,
  readFileAsBase64,
  type Draft,
  type DraftAttachment,
  type McpAppLike,
  type NewUpload,
  type PendingUpload,
  type SaveStatus,
  type Thread,
} from "./draftModel";
import { useAutoGrow, useIsMobile } from "./hooks";
import { ThreadPanel, extractThread } from "./thread";
import { RecipientFields } from "./fields";
import { AttachmentsSection } from "./attachments";
import { ComposerFooter } from "./footer";
import {
  agentBannerStyle,
  containerStyle,
  headerStyle,
  mobileThreadMessagesContainer,
  mutedStyle,
  renderStatus,
  smallPrimaryStyle,
  smallSecondaryStyle,
  statusStyle,
  successStyle,
  threadMessagesContainer,
} from "./styles";

// Preserve the public import surface: consumers import these types from
// "./Composer" (Composer.test.tsx, appContract.test.ts, main.tsx).
export type { Draft, DraftAttachment, Thread, ThreadMessage, McpAppLike } from "./draftModel";

type ComposerProps = {
  mcpApp: McpAppLike;
};

type SentState = { message_id: string };

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

      <RecipientFields
        draft={draft}
        updateField={updateField}
        showCcBcc={showCcBcc}
        setShowCcBcc={setShowCcBcc}
        isMobile={isMobile}
        bodyRef={bodyRef}
      />

      <AttachmentsSection
        draft={draft}
        uploads={uploads}
        dragActive={dragActive}
        setDragActive={setDragActive}
        fileInputRef={fileInputRef}
        onFilesChosen={onFilesChosen}
        onDrop={onDrop}
        onRemoveAttachment={onRemoveAttachment}
        dismissUpload={dismissUpload}
      />

      <ComposerFooter
        onSend={onSend}
        onSaveNow={onSaveNow}
        confirmingDiscard={confirmingDiscard}
        setConfirmingDiscard={setConfirmingDiscard}
        onDiscardConfirm={onDiscardConfirm}
      />
    </div>
  );
}
