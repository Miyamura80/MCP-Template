import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowCounterClockwise,
  EnvelopeOpen,
  Archive,
  ArrowBendUpLeft,
  ArrowBendUpRight,
  CheckCircle,
  Trash,
  PaperclipHorizontal,
  Sparkle,
  CaretLeft,
  CaretRight,
} from "@phosphor-icons/react";
import * as pdfjsLib from "pdfjs-dist";
import pdfjsWorkerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { sanitizeHtml } from "./sanitize";

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorkerSrc;

export type LabelChip = {
  name: string;
  bg_color: string;
  text_color: string;
};

export type CuratedThread = {
  thread_id: string;
  subject?: string;
  from?: string;
  snippet?: string;
  last_message_at?: string;
  importance_score: number;
  reasons: string[];
  labels?: LabelChip[];
  has_draft?: boolean;
  draft_id?: string;
};

export type Attachment = {
  filename?: string;
  mime_type?: string;
  size?: number;
  attachment_id?: string;
  content_id?: string;
  data?: string;
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
  attachments: Attachment[];
};

export type DraftAttachment = {
  filename?: string;
  mime_type?: string;
  size?: number;
  attachment_id?: string;
  message_id?: string;
};

export type Draft = {
  draft_id: string;
  to?: string;
  cc?: string;
  subject?: string;
  body?: string;
  thread_id?: string;
  attachments?: DraftAttachment[];
};

export type Thread = {
  thread_id: string;
  messages: ThreadMessage[];
  draft?: Draft;
};

export type CurateResult = { threads: CuratedThread[] };

export type McpAppLike = {
  ontoolresult?: (result: unknown) => void;
  callServerTool: (args: {
    name: string;
    arguments: Record<string, unknown>;
  }) => Promise<unknown>;
  openLink: (params: { url: string }) => Promise<unknown>;
  sendMessage?: (params: {
    role: string;
    content: { type: string; text: string }[];
  }) => Promise<unknown>;
};

type InboxProps = { mcpApp: McpAppLike };

export type ComposerDraft = {
  draft_id: string;
  to?: string;
  cc?: string;
  bcc?: string;
  subject?: string;
  body?: string;
  thread_id?: string;
  attachments?: DraftAttachment[];
};

function extractDraft(raw: unknown): ComposerDraft | null {
  if (!raw || typeof raw !== "object") return null;
  const wrapper = raw as { structuredContent?: unknown };
  const data = (wrapper.structuredContent ?? raw) as Record<string, unknown>;
  if (!data || typeof data !== "object") return null;
  const draftId = data["draft_id"];
  if (typeof draftId !== "string") return null;
  return {
    draft_id: draftId,
    to: typeof data["to"] === "string" ? data["to"] as string : undefined,
    cc: typeof data["cc"] === "string" ? data["cc"] as string : undefined,
    bcc: typeof data["bcc"] === "string" ? data["bcc"] as string : undefined,
    subject: typeof data["subject"] === "string" ? data["subject"] as string : undefined,
    body: typeof data["body"] === "string" ? data["body"] as string : undefined,
    thread_id: typeof data["thread_id"] === "string" ? data["thread_id"] as string : undefined,
    attachments: Array.isArray(data["attachments"]) ? data["attachments"] as DraftAttachment[] : undefined,
  };
}

export function Inbox({ mcpApp }: InboxProps) {
  const [viewMode, setViewMode] = useState<"inbox" | "reader">("inbox");
  const [threads, setThreads] = useState<CuratedThread[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [thread, setThread] = useState<Thread | null>(null);
  const [loadingThread, setLoadingThread] = useState(false);
  const [unreadRemoved, setUnreadRemoved] = useState<Set<string>>(new Set());
  const [markingDone, setMarkingDone] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showScores, setShowScores] = useState(true);
  const [composerDraft, setComposerDraft] = useState<ComposerDraft | null>(null);
  const composerDraftRef = useRef<ComposerDraft | null>(null);
  useEffect(() => { composerDraftRef.current = composerDraft; }, [composerDraft]);
  // Monotonic id for openThread; only the most-recent request may mutate state.
  const openSeqRef = useRef(0);

  useEffect(() => {
    let received = false;
    const handler = (raw: unknown) => {
      const draft = extractDraft(raw);
      if (draft) {
        setComposerDraft(draft);
        return;
      }
      const data = extractStructuredContent<CurateResult & Thread>(raw);
      if (data && Array.isArray(data.threads)) {
        received = true;
        setThreads(data.threads);
        setViewMode("inbox");
      } else if (data && typeof data.thread_id === "string" && Array.isArray(data.messages)) {
        received = true;
        setThread(data as unknown as Thread);
        setSelectedId(data.thread_id);
        setViewMode("reader");
        pushThreadContext(data as unknown as Thread);
      }
    };
    mcpApp.ontoolresult = handler;
    // Fallback: if the host delivered the tool result before the iframe
    // mounted (race condition), proactively fetch after a short delay.
    const timer = setTimeout(() => {
      if (!received) refresh();
    }, 800);
    return () => {
      clearTimeout(timer);
      if (mcpApp.ontoolresult === handler) mcpApp.ontoolresult = undefined;
    };
  }, [mcpApp]);

  const pushThreadContext = (data: Thread | null) => {
    if (!data || data.messages.length === 0) {
      mcpApp.callServerTool({
        name: "gmail_inbox.set_focus",
        arguments: { thread_id: null },
      });
      return;
    }
    const lastMsg = data.messages[data.messages.length - 1];
    mcpApp.callServerTool({
      name: "gmail_inbox.set_focus",
      arguments: {
        thread_id: data.thread_id,
        subject: lastMsg?.subject || null,
        from_: lastMsg?.from || null,
        message_count: data.messages.length,
        messages: data.messages.map((m) => ({
          message_id: m.message_id,
          from: m.from,
          to: m.to,
          date: m.date,
          subject: m.subject,
          body_text: m.body_text?.slice(0, 2000) || null,
        })),
      },
    });
  };

  const openThread = async (thread_id: string) => {
    const seq = ++openSeqRef.current;
    setSelectedId(thread_id);
    setThread(null);
    setLoadingThread(true);
    setError(null);
    try {
      const raw = await mcpApp.callServerTool({
        name: "gmail_inbox.open_thread",
        arguments: { thread_id },
      });
      if (seq !== openSeqRef.current) return;
      const data = extractStructuredContent<Thread>(raw);
      if (data && Array.isArray(data.messages)) {
        setThread(data);
        pushThreadContext(data);
      }
    } catch (err) {
      if (seq !== openSeqRef.current) return;
      setError(errMsg(err));
    } finally {
      if (seq === openSeqRef.current) setLoadingThread(false);
    }
  };

  const refresh = async () => {
    setStatus(null);
    setError(null);
    try {
      const raw = await mcpApp.callServerTool({
        name: "gmail_inbox.refresh",
        arguments: {},
      });
      const data = extractStructuredContent<CurateResult>(raw);
      if (data && Array.isArray(data.threads)) setThreads(data.threads);
    } catch (err) {
      setError(errMsg(err));
    }
  };

  const markRead = async () => {
    if (!selectedId) return;
    setUnreadRemoved((s) => new Set(s).add(selectedId));
    setThreads((cur) =>
      cur ? cur.map((t) =>
        t.thread_id === selectedId
          ? { ...t, labels: (t.labels || []).filter((l) => l.name !== "Unread") }
          : t,
      ) : cur,
    );
    try {
      await mcpApp.callServerTool({
        name: "gmail_inbox.mark_read",
        arguments: { thread_id: selectedId },
      });
    } catch (err) {
      setError(errMsg(err));
    }
  };

  const archive = async () => {
    if (!selectedId) return;
    const removingId = selectedId;
    setThreads((cur) =>
      cur ? cur.filter((t) => t.thread_id !== removingId) : cur
    );
    setSelectedId(null);
    setThread(null);
    pushThreadContext(null);
    try {
      await mcpApp.callServerTool({
        name: "gmail_inbox.archive",
        arguments: { thread_id: removingId },
      });
    } catch (err) {
      setError(errMsg(err));
    }
  };

  const sendReply = async (mode: "fast" | "smart", extraContext?: string) => {
    if (!selectedId || !thread) return;
    setStatus(null);
    const lastMsg = thread.messages[thread.messages.length - 1];
    const subject = lastMsg?.subject || "(no subject)";
    const from = lastMsg?.from || "the sender";
    const ctxSuffix = extraContext ? ` Additional context from the user: "${extraContext}"` : "";
    if (mcpApp.sendMessage) {
      const prompt =
        mode === "fast"
          ? `Draft a reply to the email thread "${subject}" from ${from}. Use gmail_get_focused_email for the thread context, then call gmail_reply_to_thread to create the draft. Keep it concise and direct. Do not use bash or shell commands unless explicitly instructed.${ctxSuffix}`
          : `Draft a thorough, well-researched reply to the email thread "${subject}" from ${from}. Before writing, gather all relevant context: use gmail_get_focused_email for the thread, and search across any other available tools, integrations, or knowledge sources (via MCP tools only) for information that would make the reply more informed and useful. Spawn subagents if needed to research in parallel. Do not use bash or shell commands unless explicitly instructed - all context gathering should happen through MCP tools and integrations. Once you have sufficient context, call gmail_reply_to_thread to create the draft.${ctxSuffix}`;
      setStatus(mode === "fast" ? "Drafting reply…" : "Researching context & drafting reply…");
      try {
        await mcpApp.sendMessage({
          role: "user",
          content: [{ type: "text", text: prompt }],
        });
      } catch {
        setStatus(null);
        setError("Could not trigger agent reply");
      }
    } else {
      try {
        await mcpApp.callServerTool({
          name: "gmail_inbox.reply",
          arguments: { thread_id: selectedId },
        });
        setStatus("Reply draft created");
        setTimeout(() => setStatus(null), 3000);
      } catch (err) {
        setError(errMsg(err));
      }
    }
  };
  const fastReply = (context?: string) => sendReply("fast", context);
  const smartReply = (context?: string) => sendReply("smart", context);

  const forwardMessage = async (message: ThreadMessage) => {
    if (!selectedId) return;
    setStatus(null);
    const subject = message.subject || "";
    const body = message.body_text || "";
    const header = `---------- Forwarded message ----------\nFrom: ${message.from || ""}\nDate: ${message.date || ""}\nSubject: ${subject}\nTo: ${message.to || ""}\n\n`;
    try {
      await mcpApp.callServerTool({
        name: "gmail_inbox.forward",
        arguments: {
          thread_id: selectedId,
          subject,
          body: header + body,
        },
      });
      setStatus("Forward draft created - switch to composer");
      setTimeout(() => setStatus(null), 3000);
    } catch (err) {
      setError(errMsg(err));
    }
  };

  const markDone = async (threadId?: string) => {
    const id = threadId || selectedId;
    if (!id) return;
    setMarkingDone((s) => new Set(s).add(id));
    setThreads((cur) => (cur ? cur.filter((t) => t.thread_id !== id) : cur));
    if (id === selectedId) {
      setSelectedId(null);
      setThread(null);
      pushThreadContext(null);
    }
    try {
      await mcpApp.callServerTool({
        name: "gmail_inbox.mark_done",
        arguments: { thread_id: id },
      });
    } catch (err) {
      setError(errMsg(err));
      refresh();
    } finally {
      setMarkingDone((s) => {
        const next = new Set(s);
        next.delete(id);
        return next;
      });
    }
  };

  const discardDraft = async (threadId: string, draftId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await mcpApp.callServerTool({
        name: "gmail_composer.discard",
        arguments: { draft_id: draftId },
      });
      setThreads((cur) =>
        cur
          ? cur.map((t) =>
              t.thread_id === threadId
                ? { ...t, has_draft: false, draft_id: undefined }
                : t,
            )
          : cur,
      );
    } catch (err) {
      setError(errMsg(err));
    }
  };

  const visibleThreads = threads;

  const readerContent = (
    <>
      {composerDraft ? (
        <InlineComposer
          draft={composerDraft}
          thread={thread}
          mcpApp={mcpApp}
          onDraftChange={setComposerDraft}
          onBack={() => setComposerDraft(null)}
          onDiscard={() => {
            const tid = composerDraft?.thread_id;
            setComposerDraft(null);
            if (tid) {
              setThreads((cur) =>
                cur ? cur.map((t) => t.thread_id === tid ? { ...t, has_draft: false, draft_id: undefined } : t) : cur,
              );
              setThread((cur) => cur && cur.thread_id === tid ? { ...cur, draft: undefined } : cur);
            }
          }}
          onSent={() => {
            const tid = composerDraft?.thread_id || selectedId;
            setComposerDraft(null);
            if (tid) {
              setThreads((cur) =>
                cur ? cur.map((t) => t.thread_id === tid ? { ...t, has_draft: false, draft_id: undefined } : t) : cur,
              );
              setThread((cur) => cur && cur.thread_id === tid ? { ...cur, draft: undefined } : cur);
              openThread(tid);
            }
          }}
        />
      ) : selectedId === null ? (
        <div style={mutedStyle}>Select a thread on the left.</div>
      ) : loadingThread ? (
        <div style={mutedStyle}>Loading thread…</div>
      ) : thread === null ? (
        <div style={mutedStyle}>(empty)</div>
      ) : (
        <ThreadReader
          thread={thread}
          mcpApp={mcpApp}
          onRefresh={refresh}
          onMarkRead={markRead}
          onArchive={archive}
          onMarkDone={() => markDone()}
          onFastReply={fastReply}
          onSmartReply={smartReply}
          onForward={forwardMessage}
          onEditDraft={(d) => setComposerDraft({
            draft_id: d.draft_id,
            to: d.to,
            cc: d.cc,
            subject: d.subject,
            body: d.body,
            thread_id: d.thread_id,
            attachments: d.attachments,
          })}
        />
      )}
      {status && <div style={statusStyle}>{status}</div>}
      {error && (
        <div role="alert" style={errorStyle}>
          {error}
        </div>
      )}
    </>
  );

  const aiReplyStyles = `
    .fast-reply-btn {
      display: flex; align-items: center; justify-content: center; gap: 6px;
      flex: 1; padding: 12px 16px; border: 1px solid rgba(6,182,212,0.3); border-radius: 10px;
      color: #0e7490; font-size: 14px; font-weight: 600; cursor: pointer;
      background: linear-gradient(135deg, #ecfeff, #cffafe);
      position: relative; overflow: hidden;
      transition: transform 0.15s, box-shadow 0.15s, border-color 0.15s;
    }
    .fast-reply-btn:hover {
      transform: translateY(-1px);
      border-color: rgba(6,182,212,0.5);
      box-shadow: 0 2px 12px rgba(6,182,212,0.2);
    }
    .fast-reply-btn::before {
      content: ""; position: absolute; top: 0; left: -100%; width: 200%; height: 100%;
      background: linear-gradient(90deg, transparent 0%, rgba(6,182,212,0.08) 50%, transparent 100%);
      animation: ai-shimmer 3s ease-in-out infinite;
    }
    .fast-sparkle { color: #06b6d4; animation: ai-twinkle 2.2s ease-in-out infinite; }
    .ai-reply-btn {
      display: flex; align-items: center; justify-content: center; gap: 8px;
      flex: 1.2; padding: 12px 16px; border: none; border-radius: 10px;
      color: #fff; font-size: 14px; font-weight: 600; cursor: pointer;
      position: relative; overflow: hidden;
      background: linear-gradient(135deg, #0891b2, #06b6d4, #22d3ee);
      box-shadow: 0 2px 12px rgba(6,182,212,0.4);
      transition: transform 0.15s, box-shadow 0.15s;
    }
    .ai-reply-btn:hover {
      transform: translateY(-1px);
      box-shadow: 0 4px 20px rgba(6,182,212,0.55);
    }
    .ai-reply-btn::before {
      content: ""; position: absolute; top: 0; left: -100%; width: 200%; height: 100%;
      background: linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.2) 50%, transparent 100%);
      animation: ai-shimmer 2.5s ease-in-out infinite;
    }
    @keyframes ai-shimmer {
      0% { left: -100%; }
      100% { left: 100%; }
    }
    .ai-sparkle { animation: ai-twinkle 1.8s ease-in-out infinite; }
    .ai-sparkle-sm { animation-delay: 0.6s; }
    @keyframes ai-twinkle {
      0%, 100% { opacity: 0.7; transform: scale(1) rotate(0deg); }
      50% { opacity: 1; transform: scale(1.2) rotate(15deg); }
    }
  `;

  if (viewMode === "reader") {
    return (
      <div style={{ ...appStyle, flexDirection: "column" }}>
        <style>{aiReplyStyles}</style>
        {threads && threads.length > 0 && (
          <button
            onClick={() => setViewMode("inbox")}
            style={composerBackBtnStyle}
          >
            ← Back to inbox
          </button>
        )}
        <main style={{ ...readerPaneStyle, flex: 1 }}>
          {readerContent}
        </main>
      </div>
    );
  }

  return (
    <div style={appStyle}>
      <style>{aiReplyStyles}</style>
      <aside style={listPaneStyle}>
        <header style={listHeaderStyle}>
          <strong style={{ fontSize: 14 }}>Curated inbox</strong>
          <div style={{ display: "flex", gap: 4 }}>
            <button
              onClick={() => setShowScores((v) => !v)}
              style={{ ...iconBtnStyle, fontSize: 11, fontWeight: 600, width: 28, height: 28, color: showScores ? "#1a73e8" : "#5f6368", background: showScores ? "#e8f0fe" : "#fff" }}
              title={showScores ? "Hide scores" : "Show scores"}
            >
              #
            </button>
            <button onClick={refresh} style={iconBtnStyle} title="Refresh">
              <ArrowCounterClockwise size={16} />
            </button>
          </div>
        </header>
        {visibleThreads === null ? (
          <div style={mutedStyle}>Loading inbox…</div>
        ) : visibleThreads.length === 0 ? (
          <div style={mutedStyle}>No threads.</div>
        ) : (
          <ul style={listStyle}>
            {visibleThreads.map((t) => {
              const isSelected = t.thread_id === selectedId;
              const showUnread =
                t.reasons.some((r) => r.toLowerCase().includes("unread")) &&
                !unreadRemoved.has(t.thread_id);
              return (
                <li
                  key={t.thread_id}
                  onClick={() => { setViewMode("inbox"); openThread(t.thread_id); }}
                  style={{
                    ...rowStyle,
                    background: isSelected ? "#e8f0fe" : "transparent",
                  }}
                  data-testid={`row-${t.thread_id}`}
                >
                  <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                    <SenderAvatar from={t.from} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={rowTopStyle}>
                        <span
                          style={{
                            fontWeight: showUnread ? 700 : 500,
                            flex: 1,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {t.subject || "(no subject)"}
                        </span>
                        <MarkDoneButton onClick={(e) => { e.stopPropagation(); markDone(t.thread_id); }} size="row" />
                        {showScores && (
                          <span style={chipStyle} title={t.reasons.join(", ")}>
                            {t.importance_score.toFixed(2)}
                          </span>
                        )}
                      </div>
                      {((t.labels && t.labels.length > 0) || t.reasons.length > 0 || t.has_draft) && (
                        <div style={labelChipsRowStyle}>
                          {t.has_draft && (
                            <span style={draftChipStyle}>
                              Draft
                              {t.draft_id && (
                                <button
                                  onClick={(e) => discardDraft(t.thread_id, t.draft_id!, e)}
                                  style={draftDiscardBtnStyle}
                                  title="Discard draft"
                                >
                                  ×
                                </button>
                              )}
                            </span>
                          )}
                          {t.labels?.map((l) => (
                            <span
                              key={l.name}
                              style={{
                                ...labelChipBaseStyle,
                                background: l.bg_color,
                                color: l.text_color,
                              }}
                            >
                              {l.name}
                            </span>
                          ))}
                          {t.reasons.map((r) => (
                            <span key={r} style={reasonChipStyle}>{r}</span>
                          ))}
                        </div>
                      )}
                      <div style={rowMidStyle}>{t.from || "(unknown)"}</div>
                      <div style={rowSnippetStyle}>{t.snippet || ""}</div>
                      <div style={rowFootStyle}>{relativeTime(t.last_message_at)}</div>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </aside>
      <main style={readerPaneStyle}>
        {readerContent}
      </main>
    </div>
  );
}

function ThreadReader({
  thread,
  mcpApp,
  onRefresh,
  onMarkRead,
  onArchive,
  onMarkDone,
  onFastReply,
  onSmartReply,
  onForward,
  onEditDraft,
}: {
  thread: Thread;
  mcpApp: McpAppLike;
  onRefresh: () => void;
  onMarkRead: () => void;
  onArchive: () => void;
  onMarkDone: () => void;
  onFastReply: (context?: string) => void;
  onSmartReply: (context?: string) => void;
  onForward: (message: ThreadMessage) => void;
  onEditDraft: (draft: Draft) => void;
}) {
  const [replyContext, setReplyContext] = useState("");
  const [previewData, setPreviewData] = useState<{ url: string; filename: string; mime_type: string } | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const previewBlobRef = useRef<string | null>(null);

  useEffect(() => () => {
    if (previewBlobRef.current) URL.revokeObjectURL(previewBlobRef.current);
  }, []);

  const previewSeqRef = useRef(0);

  const closePreview = () => {
    previewSeqRef.current++;
    if (previewBlobRef.current) {
      URL.revokeObjectURL(previewBlobRef.current);
      previewBlobRef.current = null;
    }
    setPreviewData(null);
    setPreviewLoading(false);
  };

  const showPreview = (b64: string, mime: string, filename: string) => {
    previewSeqRef.current++;
    if (previewBlobRef.current) URL.revokeObjectURL(previewBlobRef.current);
    const url = base64ToBlobUrl(b64, mime);
    previewBlobRef.current = url;
    setPreviewData({ url, filename, mime_type: mime });
  };

  const previewAttachment = async (att: { filename?: string; mime_type?: string; attachment_id?: string; message_id?: string; data?: string }) => {
    const mime = att.mime_type || "application/octet-stream";
    if (!isPreviewable(mime)) return;
    if (att.data) {
      showPreview(att.data, mime, att.filename || "attachment");
      return;
    }
    if (!att.attachment_id || !att.message_id) return;
    const seq = ++previewSeqRef.current;
    setPreviewLoading(true);
    try {
      const raw = await mcpApp.callServerTool({
        name: "gmail_composer.get_attachment",
        arguments: { message_id: att.message_id, attachment_id: att.attachment_id },
      });
      if (seq !== previewSeqRef.current) return;
      const parsed = extractStructuredContent<{ data_base64?: string }>(raw);
      const b64 = parsed?.data_base64;
      if (b64) {
        showPreview(b64, mime, att.filename || "attachment");
      }
    } catch { /* preview is best-effort */ }
    if (seq === previewSeqRef.current) setPreviewLoading(false);
  };
  const displayMsgs = thread.messages;
  const first = displayMsgs[0] ?? thread.messages[0];
  const subject = first?.subject || "(no subject)";
  return (
    <>
      <div style={actionsStyle}>
        <button onClick={onRefresh} style={iconBtnStyle} title="Refresh">
          <ArrowCounterClockwise size={16} />
        </button>
        <button onClick={onMarkRead} style={iconBtnStyle} title="Mark read">
          <EnvelopeOpen size={16} />
        </button>
        <button onClick={onArchive} style={iconBtnStyle} title="Archive">
          <Archive size={16} />
        </button>
        <MarkDoneButton onClick={onMarkDone} size="action" />
      </div>
      <h3 style={{ margin: "8px 0 4px 0" }}>{subject}</h3>
      <div style={mutedStyle}>
        {displayMsgs.length} message{displayMsgs.length === 1 ? "" : "s"}
      </div>
      <div style={{ marginTop: 12 }}>
        {displayMsgs.map((m, i) => (
          <CollapsibleMessage
            key={m.message_id}
            message={m}
            mcpApp={mcpApp}
            onReply={onFastReply}
            onForward={() => onForward(m)}
            onPreview={previewAttachment}
            defaultExpanded={i === displayMsgs.length - 1}
          />
        ))}
        {thread.draft && <DraftCard draft={thread.draft} onEdit={() => onEditDraft(thread.draft!)} onPreview={previewAttachment} />}
      </div>
      <div style={{ marginTop: 12 }}>
        <textarea
          value={replyContext}
          onChange={(e) => setReplyContext(e.target.value)}
          placeholder="Add context for the AI reply (optional)…"
          style={replyContextInputStyle}
          rows={2}
        />
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <button onClick={() => { onFastReply(replyContext || undefined); setReplyContext(""); }} className="fast-reply-btn" title="Quick reply using thread context only">
            <Sparkle size={16} weight="fill" className="fast-sparkle" />
            Quick reply
          </button>
          <button onClick={() => { onSmartReply(replyContext || undefined); setReplyContext(""); }} className="ai-reply-btn" title="Research context across all available sources, then draft a reply">
            <Sparkle size={18} weight="fill" className="ai-sparkle" />
            Deep context reply
            <Sparkle size={14} weight="fill" className="ai-sparkle ai-sparkle-sm" />
          </button>
        </div>
      </div>
      {previewLoading && (
        <div style={{ position: "fixed", top: 16, right: 16, padding: "8px 16px", background: "#fff", borderRadius: 8, boxShadow: "0 2px 8px rgba(0,0,0,0.15)", fontSize: 13, color: "#5f6368", zIndex: 10001 }}>
          Loading preview…
        </div>
      )}
      {previewData && (
        <div style={previewOverlayStyle} onClick={closePreview}>
          <div style={previewModalStyle} onClick={(e) => e.stopPropagation()}>
            <div style={previewHeaderStyle}>
              <span style={{ fontSize: 14, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {previewData.filename}
              </span>
              <button onClick={closePreview} style={previewCloseBtn}>×</button>
            </div>
            <div style={previewBodyStyle}>
              {previewData.mime_type === "application/pdf" ? (
                <PdfViewer url={previewData.url} />
              ) : previewData.mime_type.startsWith("image/") ? (
                <img
                  src={previewData.url}
                  alt={previewData.filename}
                  style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }}
                />
              ) : (
                <div style={{ padding: 32, textAlign: "center", color: "#5f6368" }}>
                  Preview not available for this file type.
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

type ComposerSaveStatus =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; at: Date }
  | { kind: "error"; message: string }
  | { kind: "sending" }
  | { kind: "sent"; message_id: string };

function draftFieldsEqual(a: ComposerDraft, b: ComposerDraft): boolean {
  return a.to === b.to && a.cc === b.cc && a.bcc === b.bcc && a.subject === b.subject && a.body === b.body;
}

function splitHtmlAtQuote(html: string): { main: string; quoted: string | null } {
  const markers = ['<div class="gmail_quote"', '<blockquote class="gmail_quote"', '<div class=3D"gmail_quote"'];
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
    if (onWroteRe.test(lines[i]) && i > 0)
      return { main: lines.slice(0, i).join("\n"), quoted: lines.slice(i).join("\n") };
  }
  let firstQ = -1;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].startsWith(">")) { if (firstQ === -1) firstQ = i; } else if (firstQ !== -1) break;
  }
  if (firstQ > 0 && lines.length - firstQ >= 3)
    return { main: lines.slice(0, firstQ).join("\n"), quoted: lines.slice(firstQ).join("\n") };
  return { main: text, quoted: null };
}

type FileAttachment = {
  filename: string;
  mime_type: string;
  data_base64: string;
  size: number;
};

type ExistingAttachment = {
  filename: string;
  mime_type?: string;
  size?: number;
  attachment_id?: string;
  message_id?: string;
};

function InlineComposer({
  draft,
  thread,
  mcpApp,
  onDraftChange,
  onBack,
  onDiscard,
  onSent,
}: {
  draft: ComposerDraft;
  thread: Thread | null;
  mcpApp: McpAppLike;
  onDraftChange: (d: ComposerDraft) => void;
  onBack: () => void;
  onDiscard: () => void;
  onSent: () => void;
}) {
  const [saveStatus, setSaveStatus] = useState<ComposerSaveStatus>({ kind: "idle" });
  const [showCcBcc, setShowCcBcc] = useState(false);
  const [discardHover, setDiscardHover] = useState(false);
  const [localThread, setLocalThread] = useState<Thread | null>(thread);
  const [loadingThread, setLoadingThread] = useState(false);
  const [pendingAgent, setPendingAgent] = useState<ComposerDraft | null>(null);
  const [attachments, setAttachments] = useState<FileAttachment[]>([]);
  const [existingAttachments, setExistingAttachments] = useState<ExistingAttachment[]>(
    () => (draft.attachments || [])
      .filter((a): a is DraftAttachment & { filename: string } => !!a.filename)
      .map((a) => ({ filename: a.filename, mime_type: a.mime_type, size: a.size, attachment_id: a.attachment_id, message_id: a.message_id })),
  );
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [previewData, setPreviewData] = useState<{ url: string; filename: string; mime_type: string } | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const previewBlobRef = useRef<string | null>(null);

  useEffect(() => () => {
    if (previewBlobRef.current) URL.revokeObjectURL(previewBlobRef.current);
  }, []);

  const localDirtyRef = useRef(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const draftRef = useRef(draft);
  useEffect(() => { draftRef.current = draft; }, [draft]);

  // Listen for agent-initiated draft updates via ontoolresult
  useEffect(() => {
    const prevHandler = mcpApp.ontoolresult;
    const handler = (raw: unknown) => {
      const incoming = extractDraft(raw);
      if (!incoming) {
        if (prevHandler) prevHandler(raw);
        return;
      }
      const current = draftRef.current;
      if (localDirtyRef.current && current && !draftFieldsEqual(current, incoming)) {
        setPendingAgent(incoming);
        return;
      }
      onDraftChange(incoming);
      localDirtyRef.current = false;
    };
    mcpApp.ontoolresult = handler;
    return () => { if (mcpApp.ontoolresult === handler) mcpApp.ontoolresult = prevHandler; };
  }, [mcpApp]);

  // Auto-fetch thread context
  useEffect(() => {
    if (localThread || loadingThread) return;
    const threadId = draft.thread_id;
    if (!threadId) return;
    let cancelled = false;
    setLoadingThread(true);
    mcpApp.callServerTool({
      name: "gmail_composer.get_thread",
      arguments: { thread_id: threadId },
    }).then((raw) => {
      if (cancelled) return;
      const data = (raw as { structuredContent?: unknown })?.structuredContent ?? raw;
      const t = data as Thread | null;
      if (t && Array.isArray(t.messages)) setLocalThread(t);
    }).catch(() => {}).finally(() => { if (!cancelled) setLoadingThread(false); });
    return () => { cancelled = true; };
  }, [draft.thread_id]);

  // Auto-refresh draft fields if they arrived empty
  useEffect(() => {
    if (!draft.draft_id) return;
    if (draft.to || draft.subject || draft.body) return;
    let cancelled = false;
    mcpApp.callServerTool({
      name: "gmail_composer.refresh",
      arguments: { draft_id: draft.draft_id },
    }).then((raw) => {
      if (cancelled) return;
      const data = (raw as { structuredContent?: unknown })?.structuredContent ?? raw;
      const d = data as ComposerDraft | null;
      if (d && d.draft_id) onDraftChange({ ...draft, ...d });
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [draft.draft_id]);

  // Sync existing attachments when draft updates (e.g. from refresh or agent)
  useEffect(() => {
    if (!draft.attachments?.length) return;
    setExistingAttachments(
      draft.attachments
        .filter((a): a is DraftAttachment & { filename: string } => !!a.filename)
        .map((a) => ({ filename: a.filename, mime_type: a.mime_type, size: a.size, attachment_id: a.attachment_id, message_id: a.message_id })),
    );
  }, [draft.attachments]);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    Array.from(files).forEach((file) => {
      const reader = new FileReader();
      reader.onload = () => {
        const result = reader.result as string;
        const base64 = result.split(",")[1] || "";
        setAttachments((prev) => [
          ...prev,
          { filename: file.name, mime_type: file.type || "application/octet-stream", data_base64: base64, size: file.size },
        ]);
      };
      reader.readAsDataURL(file);
    });
    e.target.value = "";
  };

  const removeAttachment = (index: number) => {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  };

  const previewSeqRef = useRef(0);

  const closePreview = () => {
    previewSeqRef.current++;
    if (previewBlobRef.current) {
      URL.revokeObjectURL(previewBlobRef.current);
      previewBlobRef.current = null;
    }
    setPreviewData(null);
    setPreviewLoading(false);
  };

  const showPreview = (b64: string, mime: string, filename: string) => {
    previewSeqRef.current++;
    if (previewBlobRef.current) URL.revokeObjectURL(previewBlobRef.current);
    const url = base64ToBlobUrl(b64, mime);
    previewBlobRef.current = url;
    setPreviewData({ url, filename, mime_type: mime });
  };

  const previewNewAttachment = (att: FileAttachment) => {
    showPreview(att.data_base64, att.mime_type, att.filename);
  };

  const previewExistingAttachment = async (att: ExistingAttachment) => {
    if (!att.attachment_id || !att.message_id) return;
    const seq = ++previewSeqRef.current;
    setPreviewLoading(true);
    try {
      const raw = await mcpApp.callServerTool({
        name: "gmail_composer.get_attachment",
        arguments: { message_id: att.message_id, attachment_id: att.attachment_id },
      });
      if (seq !== previewSeqRef.current) return;
      const parsed = extractStructuredContent<{ data_base64?: string }>(raw);
      const b64 = parsed?.data_base64;
      if (b64) {
        showPreview(b64, att.mime_type || "application/octet-stream", att.filename);
      }
    } catch { /* preview is best-effort */ }
    if (seq === previewSeqRef.current) setPreviewLoading(false);
  };

  const persistDraft = async (d: ComposerDraft) => {
    setSaveStatus({ kind: "saving" });
    const snapshot = d;
    try {
      const args: Record<string, unknown> = {
        draft_id: snapshot.draft_id,
        to: snapshot.to ?? "",
        cc: snapshot.cc ?? "",
        bcc: snapshot.bcc ?? "",
        subject: snapshot.subject ?? "",
        body: snapshot.body ?? "",
      };
      if (attachments.length > 0) {
        args.attachments = attachments.map(({ filename, mime_type, data_base64 }) => ({ filename, mime_type, data_base64 }));
      }
      await mcpApp.callServerTool({ name: "gmail_composer.save_draft", arguments: args });
      setSaveStatus({ kind: "saved", at: new Date() });
      const latest = draftRef.current;
      if (latest && draftFieldsEqual(latest, snapshot)) localDirtyRef.current = false;
    } catch (err) {
      setSaveStatus({ kind: "error", message: errMsg(err) });
    }
  };

  const scheduleAutoSave = (next: ComposerDraft) => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => void persistDraft(next), 800);
  };

  const updateField = (key: keyof ComposerDraft, value: string) => {
    const next = { ...draft, [key]: value };
    onDraftChange(next);
    localDirtyRef.current = true;
    scheduleAutoSave(next);
  };

  const onSend = async () => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    setSaveStatus({ kind: "sending" });
    try {
      const args: Record<string, unknown> = {
        draft_id: draft.draft_id,
        to: draft.to ?? "",
        cc: draft.cc ?? "",
        bcc: draft.bcc ?? "",
        subject: draft.subject ?? "",
        body: draft.body ?? "",
      };
      if (attachments.length > 0) {
        args.attachments = attachments.map(({ filename, mime_type, data_base64 }) => ({ filename, mime_type, data_base64 }));
      }
      const raw = await mcpApp.callServerTool({ name: "gmail_composer.send", arguments: args });
      const wrapper = (raw ?? {}) as { structuredContent?: { message_id?: string } };
      const inner = wrapper.structuredContent ?? (raw as { message_id?: string });
      const msgId = (inner as { message_id?: string })?.message_id ?? "";
      setSaveStatus({ kind: "sent", message_id: msgId });
      setTimeout(onSent, 1500);
    } catch (err) {
      setSaveStatus({ kind: "error", message: errMsg(err) });
    }
  };

  const onDiscardNow = async () => {
    if (saveTimerRef.current) { clearTimeout(saveTimerRef.current); saveTimerRef.current = null; }
    onDiscard();
    try {
      await mcpApp.callServerTool({
        name: "gmail_composer.discard",
        arguments: { draft_id: draft.draft_id },
      });
    } catch { /* discard is best-effort */ }
  };

  const applyAgentUpdate = () => {
    if (!pendingAgent) return;
    if (saveTimerRef.current) { clearTimeout(saveTimerRef.current); saveTimerRef.current = null; }
    onDraftChange(pendingAgent);
    setPendingAgent(null);
    localDirtyRef.current = false;
  };

  if (saveStatus.kind === "sent") {
    return (
      <div style={{ padding: 16 }}>
        <div style={composerSentStyle}>
          <CheckCircle size={20} weight="fill" style={{ marginRight: 6, verticalAlign: "middle" }} />
          Message sent
        </div>
      </div>
    );
  }

  const effectiveThread = localThread || thread;
  const allMsgs = effectiveThread?.messages ?? [];
  const sentMessages = allMsgs;
  const first = sentMessages[0] ?? allMsgs[0];
  const subject = first?.subject || draft.subject || "(no subject)";

  return (
    <div style={{ fontFamily: "'Google Sans', Roboto, Arial, sans-serif" }}>
      <button onClick={onBack} style={composerBackBtnStyle}>
        ← Back to inbox
      </button>

      <h2 style={composerSubjectStyle}>{subject}</h2>

      {loadingThread && (
        <div style={{ color: "#5f6368", fontSize: 13, padding: "8px 0" }}>Loading conversation…</div>
      )}
      {sentMessages.length > 0 && (
        <ComposerThreadPanel thread={{ ...effectiveThread!, messages: sentMessages }} />
      )}

      {pendingAgent && (
        <div style={composerAgentBanner}>
          <span>Agent updated this draft.</span>
          <button onClick={applyAgentUpdate} style={composerAgentApplyBtn}>Apply</button>
          <button onClick={() => setPendingAgent(null)} style={composerAgentKeepBtn}>Keep mine</button>
        </div>
      )}

      {/* --- Compose card (Gmail Material 3 elevation) --- */}
      <div style={composerCardStyle}>
        <div style={composerFieldRow}>
          <span style={composerFieldLabel}>To</span>
          <input
            type="text"
            value={draft.to ?? ""}
            onChange={(e) => updateField("to", e.target.value)}
            style={composerInputStyle}
            aria-label="To"
          />
          {!showCcBcc && (
            <button onClick={() => setShowCcBcc(true)} style={composerCcBccToggle}>Cc/Bcc</button>
          )}
        </div>

        {showCcBcc && (
          <>
            <div style={composerFieldRow}>
              <span style={composerFieldLabel}>Cc</span>
              <input type="text" value={draft.cc ?? ""} onChange={(e) => updateField("cc", e.target.value)} style={composerInputStyle} aria-label="Cc" />
            </div>
            <div style={composerFieldRow}>
              <span style={composerFieldLabel}>Bcc</span>
              <input type="text" value={draft.bcc ?? ""} onChange={(e) => updateField("bcc", e.target.value)} style={composerInputStyle} aria-label="Bcc" />
            </div>
          </>
        )}

        <div style={composerFieldDivider} />

        <textarea
          value={draft.body ?? ""}
          onChange={(e) => updateField("body", e.target.value)}
          rows={12}
          style={composerTextareaStyle}
          aria-label="Body"
          placeholder="Compose your reply…"
        />

        {/* Attachments list (existing + newly added) */}
        {(existingAttachments.length > 0 || attachments.length > 0) && (
          <div style={{ padding: "8px 16px", display: "flex", flexWrap: "wrap", gap: 6 }}>
            {existingAttachments.map((att, i) => (
              <div
                key={`existing-${i}`}
                style={{ ...attachmentChipStyle, cursor: isPreviewable(att.mime_type) ? "pointer" : "default" }}
                onClick={() => isPreviewable(att.mime_type) && previewExistingAttachment(att)}
                title={isPreviewable(att.mime_type) ? "Click to preview" : att.filename}
              >
                <PaperclipHorizontal size={12} style={{ marginRight: 4, flexShrink: 0 }} />
                <span style={{ fontSize: 12, maxWidth: 150, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {att.filename}
                </span>
                {att.size != null && (
                  <span style={{ fontSize: 11, color: "#5f6368", marginLeft: 4 }}>
                    {formatFileSize(att.size)}
                  </span>
                )}
              </div>
            ))}
            {attachments.map((att, i) => (
              <div
                key={`new-${i}`}
                style={{ ...attachmentChipStyle, cursor: isPreviewable(att.mime_type) ? "pointer" : "default" }}
                onClick={() => isPreviewable(att.mime_type) && previewNewAttachment(att)}
                title={isPreviewable(att.mime_type) ? "Click to preview" : att.filename}
              >
                <PaperclipHorizontal size={12} style={{ marginRight: 4, flexShrink: 0 }} />
                <span style={{ fontSize: 12, maxWidth: 150, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {att.filename}
                </span>
                <span style={{ fontSize: 11, color: "#5f6368", marginLeft: 4 }}>
                  {formatFileSize(att.size)}
                </span>
                <button onClick={(e) => { e.stopPropagation(); removeAttachment(i); }} style={attachmentRemoveBtn} title="Remove">×</button>
              </div>
            ))}
          </div>
        )}

        {previewLoading && (
          <div style={{ padding: "8px 16px", fontSize: 13, color: "#5f6368" }}>Loading preview…</div>
        )}

        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          style={{ display: "none" }}
          onChange={handleFileSelect}
        />

        {/* Toolbar */}
        <div style={composerToolbarStyle}>
          <div style={composerToolbarLeft}>
            <button onClick={onSend} style={composerSendBtnStyle}>
              {saveStatus.kind === "sending" ? "Sending…" : "Send"}
            </button>
            <button style={composerToolbarIconBtn} title="Attach files" onClick={() => fileInputRef.current?.click()}>
              <PaperclipHorizontal size={18} />
            </button>
          </div>
          <div style={composerToolbarRight}>
            <span style={composerSaveStatusStyle(saveStatus)}>
              {renderComposerStatus(saveStatus)}
            </span>
            <button
              onClick={onDiscardNow}
              style={{
                ...composerTrashBtn,
                color: discardHover ? "#d93025" : "#5f6368",
                background: discardHover ? "#fce8e6" : "transparent",
              }}
              title="Discard draft"
              onMouseEnter={() => setDiscardHover(true)}
              onMouseLeave={() => setDiscardHover(false)}
            >
              <Trash size={18} />
            </button>
          </div>
        </div>
      </div>

      {/* Attachment preview modal */}
      {previewData && (
        <div style={previewOverlayStyle} onClick={closePreview}>
          <div style={previewModalStyle} onClick={(e) => e.stopPropagation()}>
            <div style={previewHeaderStyle}>
              <span style={{ fontSize: 14, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {previewData.filename}
              </span>
              <button onClick={closePreview} style={previewCloseBtn}>×</button>
            </div>
            <div style={previewBodyStyle}>
              {previewData.mime_type === "application/pdf" ? (
                <PdfViewer url={previewData.url} />
              ) : previewData.mime_type.startsWith("image/") ? (
                <img
                  src={previewData.url}
                  alt={previewData.filename}
                  style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }}
                />
              ) : (
                <div style={{ padding: 32, textAlign: "center", color: "#5f6368" }}>
                  Preview not available for this file type.
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ComposerThreadPanel({ thread }: { thread: Thread }) {
  return (
    <div style={composerThreadPanelStyle}>
      {thread.messages.map((m, i) => (
        <ComposerThreadMsg key={m.message_id} message={m} defaultExpanded={i === thread.messages.length - 1} />
      ))}
    </div>
  );
}

function ComposerThreadMsg({ message, defaultExpanded }: { message: ThreadMessage; defaultExpanded: boolean }) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  if (!expanded) {
    return (
      <div style={composerCollapsedMsgStyle} onClick={() => setExpanded(true)}>
        <SenderAvatar from={message.from} />
        <strong style={{ fontSize: 13 }}>{message.from || "(unknown)"}</strong>
        <span style={{ color: "#5f6368", fontSize: 12, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {message.body_text?.slice(0, 80) || message.subject || ""}
        </span>
        <span style={{ color: "#5f6368", fontSize: 11, flexShrink: 0 }}>{relativeTime(message.date)}</span>
      </div>
    );
  }

  return (
    <div style={composerExpandedMsgStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8, cursor: "pointer" }} onClick={() => setExpanded(false)}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <SenderAvatar from={message.from} />
          <div>
            <strong style={{ fontSize: 14 }}>{message.from || "(unknown)"}</strong>
            {message.to && <div style={{ fontSize: 12, color: "#5f6368" }}>to {message.to}</div>}
          </div>
        </div>
        <span style={{ fontSize: 12, color: "#5f6368" }}>{relativeTime(message.date)}</span>
      </div>
      <ComposerMsgBody message={message} />
    </div>
  );
}

function ComposerMsgBody({ message }: { message: ThreadMessage }) {
  const [showQuoted, setShowQuoted] = useState(false);

  if (message.body_html) {
    const { main, quoted } = splitHtmlAtQuote(message.body_html);
    return (
      <div>
        <div style={composerBodyHtmlStyle} dangerouslySetInnerHTML={{ __html: sanitizeHtml(main) }} />
        {quoted && (
          <>
            <button onClick={() => setShowQuoted((v) => !v)} style={composerQuoteToggle}>&bull;&bull;&bull;</button>
            {showQuoted && (
              <div style={{ ...composerBodyHtmlStyle, borderLeft: "3px solid #dadce0", paddingLeft: 8, marginTop: 4 }} dangerouslySetInnerHTML={{ __html: sanitizeHtml(quoted) }} />
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
        <pre style={composerBodyTextStyle}>{main}</pre>
        {quoted && (
          <>
            <button onClick={() => setShowQuoted((v) => !v)} style={composerQuoteToggle}>&bull;&bull;&bull;</button>
            {showQuoted && <pre style={{ ...composerBodyTextStyle, borderLeft: "3px solid #dadce0", paddingLeft: 8, marginTop: 4 }}>{quoted}</pre>}
          </>
        )}
      </div>
    );
  }
  return <div style={{ color: "#5f6368", fontSize: 13 }}>(no body)</div>;
}

function renderComposerStatus(s: ComposerSaveStatus): string {
  switch (s.kind) {
    case "idle": return "";
    case "saving": return "Saving…";
    case "saved": return `Saved at ${s.at.getHours().toString().padStart(2, "0")}:${s.at.getMinutes().toString().padStart(2, "0")}`;
    case "error": return `Error: ${s.message}`;
    case "sending": return "Sending…";
    case "sent": return "Sent!";
  }
}

function MarkDoneButton({ onClick, size = "row" }: { onClick: (e: React.MouseEvent) => void; size?: "row" | "action" }) {
  const [hovered, setHovered] = useState(false);
  const isAction = size === "action";
  const baseStyle: React.CSSProperties = isAction
    ? {
        ...iconBtnStyle,
        background: hovered ? "#e6f4ea" : "#fff",
        borderColor: hovered ? "#34a853" : "#dadce0",
        color: hovered ? "#137333" : "#5f6368",
        transform: hovered ? "scale(1.15)" : "scale(1)",
        transition: "all 0.15s ease",
      }
    : {
        background: "none",
        border: "none",
        color: hovered ? "#137333" : "#aaa",
        padding: 2,
        cursor: "pointer",
        display: "inline-flex",
        alignItems: "center",
        flexShrink: 0,
        borderRadius: 4,
        transform: hovered ? "scale(1.3)" : "scale(1)",
        transition: "all 0.15s ease",
        backgroundColor: hovered ? "#e6f4ea" : "transparent",
      };
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={baseStyle}
      title="Mark done"
    >
      <CheckCircle size={isAction ? 16 : 14} weight={hovered ? "fill" : "regular"} />
    </button>
  );
}

function SenderAvatar({ from }: { from: string | undefined }) {
  const name = from || "";
  const match = name.match(/^([^<]*)/);
  const display = (match?.[1] || name).trim();
  const initials = display
    ? display
        .split(/[\s.]+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((w) => w[0].toUpperCase())
        .join("")
    : "?";
  const hue = [...(display || "?")].reduce((h, c) => h + c.charCodeAt(0), 0) % 360;
  return (
    <div
      style={{
        width: 32,
        height: 32,
        borderRadius: "50%",
        background: `hsl(${hue}, 55%, 55%)`,
        color: "#fff",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 12,
        fontWeight: 600,
        flexShrink: 0,
        letterSpacing: 0.5,
      }}
      title={from || "(unknown)"}
    >
      {initials}
    </div>
  );
}

function CollapsibleMessage({
  message,
  mcpApp,
  onReply,
  onForward,
  onPreview,
  defaultExpanded,
}: {
  message: ThreadMessage;
  mcpApp: McpAppLike;
  onReply: () => void;
  onForward: () => void;
  onPreview: (att: { filename?: string; mime_type?: string; attachment_id?: string; message_id?: string; data?: string }) => void;
  defaultExpanded: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  if (!expanded) {
    return (
      <div
        style={collapsedMessageStyle}
        onClick={() => setExpanded(true)}
        data-testid={`collapsed-${message.message_id}`}
      >
        <SenderAvatar from={message.from} />
        <strong style={{ fontSize: 13 }}>{message.from || "(unknown)"}</strong>
        <span style={{ color: "#888", fontSize: 12, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {message.body_text?.slice(0, 80) || message.subject || ""}
        </span>
        <span style={{ color: "#999", fontSize: 11, flexShrink: 0 }} title={formatDate(message.date)}>
          {relativeTime(message.date)}
        </span>
        <button
          style={collapsedReplyBtnStyle}
          title="Reply"
          onClick={(e) => { e.stopPropagation(); onReply(); }}
        >
          <ArrowBendUpLeft size={14} />
        </button>
      </div>
    );
  }

  return (
    <MessageView
      message={message}
      mcpApp={mcpApp}
      onReply={onReply}
      onForward={onForward}
      onPreview={onPreview}
      onCollapse={() => setExpanded(false)}
    />
  );
}

function MessageView({
  message,
  mcpApp,
  onReply,
  onForward,
  onPreview,
  onCollapse,
}: {
  message: ThreadMessage;
  mcpApp: McpAppLike;
  onReply: () => void;
  onForward: () => void;
  onPreview: (att: { filename?: string; mime_type?: string; attachment_id?: string; message_id?: string; data?: string }) => void;
  onCollapse?: () => void;
}) {
  // Non-inline image attachments (inline ones are already in the HTML via data URIs)
  const imageAttachments = message.attachments.filter(
    (a) => a.mime_type?.startsWith("image/") && !a.content_id
  );
  const otherAttachments = message.attachments.filter(
    (a) => !a.mime_type?.startsWith("image/")
  );

  return (
    <article style={messageStyle} data-testid={`msg-${message.message_id}`}>
      <header
        style={{ ...messageHeaderStyle, cursor: onCollapse ? "pointer" : undefined }}
        onClick={onCollapse}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <SenderAvatar from={message.from} />
          <div>
            <strong>{message.from || "(unknown)"}</strong>
            {message.to && (
              <div style={{ fontSize: 12, color: "#666" }}>to {message.to}</div>
            )}
            {message.cc && (
              <div style={{ fontSize: 12, color: "#666" }}>cc {message.cc}</div>
            )}
          </div>
        </div>
        <div
          style={{ color: "#666", fontSize: 12, flexShrink: 0 }}
          title={formatDate(message.date)}
        >
          {relativeTime(message.date)}
        </div>
      </header>
      <MessageBody message={message} mcpApp={mcpApp} />
      {imageAttachments.length > 0 && (
        <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 8 }}>
          {imageAttachments.map((a, i) => (
            <div
              key={a.attachment_id || `${message.message_id}-img-${i}`}
              style={{ ...imageAttachmentStyle, cursor: "pointer" }}
              data-testid="image-attachment"
              onClick={() => onPreview({ ...a, message_id: message.message_id })}
              title="Click to preview"
            >
              {a.data ? (
                <img
                  src={`data:${a.mime_type || "image/png"};base64,${a.data}`}
                  alt={a.filename || "attachment"}
                  style={{ maxWidth: "100%", borderRadius: 4 }}
                />
              ) : (
                <div style={{ padding: 8, textAlign: "center" }}>
                  <div style={{ fontSize: 11, color: "#555" }}>
                    {a.filename || "image"}
                  </div>
                  <div style={{ fontSize: 11, color: "#999" }}>
                    {a.mime_type}{typeof a.size === "number" && ` · ${formatSize(a.size)}`}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {otherAttachments.length > 0 && (
        <div style={attachmentsRowStyle}>
          {otherAttachments.map((a, i) => (
            <span
              key={a.attachment_id || `${message.message_id}-att-${i}`}
              style={{ ...attachmentChipStyle, cursor: isPreviewable(a.mime_type) ? "pointer" : "default" }}
              data-testid="attachment-chip"
              onClick={() => isPreviewable(a.mime_type) && onPreview({ ...a, message_id: message.message_id })}
              title={isPreviewable(a.mime_type) ? "Click to preview" : a.filename || "(file)"}
            >
              {a.filename || "(file)"}
              {typeof a.size === "number" && (
                <span style={{ color: "#888", marginLeft: 6 }}>
                  {formatSize(a.size)}
                </span>
              )}
            </span>
          ))}
        </div>
      )}
      <div style={messageActionsStyle}>
        <button onClick={onReply} style={messageActionBtnStyle} title="Reply">
          <ArrowBendUpLeft size={14} /> Reply
        </button>
        <button onClick={onForward} style={messageActionBtnStyle} title="Forward">
          <ArrowBendUpRight size={14} /> Forward
        </button>
      </div>
    </article>
  );
}

function MessageBody({
  message,
  mcpApp,
}: {
  message: ThreadMessage;
  mcpApp: McpAppLike;
}) {
  const [showQuoted, setShowQuoted] = useState(false);

  const handleLinkClick = (e: React.MouseEvent<HTMLElement>) => {
    const anchor = (e.target as HTMLElement).closest("a");
    if (!anchor) return;
    const href = anchor.getAttribute("href");
    if (!href) return;
    e.preventDefault();
    e.stopPropagation();
    try {
      const scheme = new URL(href).protocol;
      if (!["https:", "http:", "mailto:"].includes(scheme)) return;
    } catch {
      return;
    }
    mcpApp.openLink({ url: href });
  };

  if (message.body_html) {
    const { main, quoted } = splitHtmlAtQuote(message.body_html);
    return (
      <div>
        <div
          style={bodyHtmlStyle}
          dangerouslySetInnerHTML={{ __html: sanitizeHtml(main) }}
          onClick={handleLinkClick}
        />
        {quoted && (
          <>
            <button
              onClick={() => setShowQuoted((v) => !v)}
              style={quoteToggleStyle}
              title={showQuoted ? "Hide quoted text" : "Show quoted text"}
            >
              •••
            </button>
            {showQuoted && (
              <div
                style={{ ...bodyHtmlStyle, borderLeft: "3px solid #dadce0", paddingLeft: 10, marginTop: 4 }}
                dangerouslySetInnerHTML={{ __html: sanitizeHtml(quoted) }}
                onClick={handleLinkClick}
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
        <pre style={bodyTextStyle}>{main}</pre>
        {quoted && (
          <>
            <button
              onClick={() => setShowQuoted((v) => !v)}
              style={quoteToggleStyle}
              title={showQuoted ? "Hide quoted text" : "Show quoted text"}
            >
              •••
            </button>
            {showQuoted && (
              <pre style={{ ...bodyTextStyle, borderLeft: "3px solid #dadce0", paddingLeft: 10, marginTop: 4 }}>{quoted}</pre>
            )}
          </>
        )}
      </div>
    );
  }
  return <div style={mutedStyle}>(no body)</div>;
}

function DraftCard({ draft, onEdit, onPreview }: { draft: Draft; onEdit?: () => void; onPreview?: (att: { filename?: string; mime_type?: string; attachment_id?: string; message_id?: string }) => void }) {
  const atts = draft.attachments?.filter((a) => a.filename) ?? [];
  return (
    <article
      style={{ ...draftCardStyle, cursor: onEdit ? "pointer" : undefined }}
      data-testid="draft-card"
      onClick={onEdit}
      title={onEdit ? "Click to edit draft" : undefined}
    >
      <header style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <span style={draftChipStyle}>Draft</span>
        {draft.to && (
          <span style={{ fontSize: 12, color: "#666" }}>to {draft.to}</span>
        )}
        {onEdit && (
          <span style={{ marginLeft: "auto", fontSize: 12, color: "#c5221f", fontWeight: 500 }}>Edit ›</span>
        )}
      </header>
      {draft.subject && (
        <div style={{ fontWeight: 600, marginBottom: 6 }}>{draft.subject}</div>
      )}
      {draft.body ? (
        <pre style={draftBodyStyle}>{draft.body}</pre>
      ) : (
        <div style={mutedStyle}>(empty draft)</div>
      )}
      {atts.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
          {atts.map((a, i) => (
            <div
              key={i}
              style={{ ...attachmentChipStyle, cursor: onPreview && isPreviewable(a.mime_type) ? "pointer" : "default" }}
              onClick={(e) => {
                if (onPreview && isPreviewable(a.mime_type)) {
                  e.stopPropagation();
                  onPreview(a);
                }
              }}
              title={isPreviewable(a.mime_type) ? "Click to preview" : a.filename || ""}
            >
              <PaperclipHorizontal size={12} style={{ marginRight: 4, flexShrink: 0 }} />
              <span style={{ fontSize: 12, maxWidth: 150, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {a.filename}
              </span>
              {a.size != null && (
                <span style={{ fontSize: 11, color: "#5f6368", marginLeft: 4 }}>
                  {formatFileSize(a.size)}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </article>
  );
}

function extractStructuredContent<T>(raw: unknown): T | null {
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

function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function isPreviewable(mime?: string): boolean {
  if (!mime) return false;
  return mime === "application/pdf" || mime.startsWith("image/");
}

function base64ToBlobUrl(b64: string, mime: string): string {
  const raw = atob(b64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return URL.createObjectURL(new Blob([arr], { type: mime }));
}

function PdfViewer({ url }: { url: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [page, setPage] = useState(1);
  const [numPages, setNumPages] = useState(0);
  const [pdfDoc, setPdfDoc] = useState<pdfjsLib.PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(url)
      .then((r) => r.arrayBuffer())
      .then((buf) => {
        if (cancelled) return;
        return pdfjsLib.getDocument({ data: new Uint8Array(buf), isEvalSupported: false }).promise;
      })
      .then((doc) => {
        if (cancelled || !doc) return;
        setPdfDoc(doc);
        setNumPages(doc.numPages);
        setPage(1);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load PDF");
      });
    return () => { cancelled = true; };
  }, [url]);

  const renderPage = useCallback(async (doc: pdfjsLib.PDFDocumentProxy, pageNum: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const pg = await doc.getPage(pageNum);
    const container = canvas.parentElement;
    const containerWidth = container ? container.clientWidth - 16 : 600;
    const unscaled = pg.getViewport({ scale: 1 });
    const scale = containerWidth / unscaled.width;
    const viewport = pg.getViewport({ scale });
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    await pg.render({ canvasContext: ctx, viewport }).promise;
  }, []);

  useEffect(() => {
    if (pdfDoc && page >= 1 && page <= numPages) {
      renderPage(pdfDoc, page);
    }
  }, [pdfDoc, page, numPages, renderPage]);

  if (error) return <div style={{ padding: 32, textAlign: "center", color: "#d93025" }}>{error}</div>;
  if (!pdfDoc) return <div style={{ padding: 32, textAlign: "center", color: "#5f6368" }}>Loading PDF…</div>;

  return (
    <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column" }}>
      {numPages > 1 && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 12, padding: "6px 0", borderBottom: "1px solid #e0e0e0", flexShrink: 0 }}>
          <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1} style={pdfNavBtn} title="Previous page">
            <CaretLeft size={16} />
          </button>
          <span style={{ fontSize: 13, color: "#444" }}>{page} / {numPages}</span>
          <button onClick={() => setPage((p) => Math.min(numPages, p + 1))} disabled={page >= numPages} style={pdfNavBtn} title="Next page">
            <CaretRight size={16} />
          </button>
        </div>
      )}
      <div style={{ flex: 1, overflow: "auto", display: "flex", justifyContent: "center", padding: 8 }}>
        <canvas ref={canvasRef} style={{ maxWidth: "100%" }} />
      </div>
    </div>
  );
}

const pdfNavBtn: React.CSSProperties = {
  background: "none",
  border: "1px solid #dadce0",
  borderRadius: 4,
  padding: "4px 8px",
  cursor: "pointer",
  display: "flex",
  alignItems: "center",
};

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

function formatDate(iso: string | undefined): string {
  if (!iso) return "";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso;
  return dt.toLocaleString();
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// --------- styles ---------

const appStyle: React.CSSProperties = {
  fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
  display: "flex",
  height: "min(100vh, 480px)",
  maxHeight: 480,
  width: "100%",
  color: "#202124",
  background: "#fff",
  borderRadius: 8,
  overflow: "hidden",
  colorScheme: "light",
};

const listPaneStyle: React.CSSProperties = {
  width: "38%",
  borderRight: "1px solid #e0e0e0",
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
};

const listHeaderStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "10px 12px",
  borderBottom: "1px solid #eee",
  color: "#202124",
};

const listStyle: React.CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  overflowY: "auto",
  flex: 1,
};

const rowStyle: React.CSSProperties = {
  padding: "10px 12px",
  borderBottom: "1px solid #f0f0f0",
  cursor: "pointer",
  color: "#202124",
};

const rowTopStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const rowMidStyle: React.CSSProperties = {
  fontSize: 12,
  color: "#555",
  marginTop: 2,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const rowSnippetStyle: React.CSSProperties = {
  fontSize: 12,
  color: "#888",
  marginTop: 2,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const rowFootStyle: React.CSSProperties = {
  fontSize: 11,
  color: "#999",
  marginTop: 4,
};

const chipStyle: React.CSSProperties = {
  background: "#f1f3f4",
  color: "#444",
  fontSize: 11,
  padding: "2px 6px",
  borderRadius: 10,
  flexShrink: 0,
};

const labelChipsRowStyle: React.CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 4,
  marginTop: 4,
};

const labelChipBaseStyle: React.CSSProperties = {
  fontSize: 10,
  padding: "1px 6px",
  borderRadius: 8,
  whiteSpace: "nowrap",
  fontWeight: 500,
};

const reasonChipStyle: React.CSSProperties = {
  ...labelChipBaseStyle,
  background: "#f1f3f4",
  color: "#5f6368",
};

const draftChipStyle: React.CSSProperties = {
  ...labelChipBaseStyle,
  background: "#fce8e6",
  color: "#c5221f",
  fontWeight: 600,
  display: "inline-flex",
  alignItems: "center",
  gap: 3,
};

const draftDiscardBtnStyle: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "#c5221f",
  cursor: "pointer",
  padding: 0,
  fontSize: 13,
  fontWeight: 700,
  lineHeight: 1,
  display: "inline-flex",
  alignItems: "center",
  opacity: 0.6,
  marginLeft: 2,
};

const replyContextInputStyle: React.CSSProperties = {
  width: "100%",
  padding: "8px 12px",
  border: "1px solid #dadce0",
  borderRadius: 8,
  fontSize: 13,
  fontFamily: "'Google Sans', Roboto, Arial, sans-serif",
  color: "#202124",
  resize: "vertical",
  outline: "none",
  boxSizing: "border-box",
};

const readerPaneStyle: React.CSSProperties = {
  flex: 1,
  padding: 16,
  overflowY: "auto",
  color: "#202124",
};

const actionsStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  alignItems: "center",
  marginBottom: 8,
};

const smallBtnStyle: React.CSSProperties = {
  background: "#fff",
  border: "1px solid #dadce0",
  color: "#202124",
  padding: "4px 10px",
  borderRadius: 6,
  cursor: "pointer",
  fontSize: 12,
};

const iconBtnStyle: React.CSSProperties = {
  background: "#fff",
  border: "1px solid #dadce0",
  color: "#5f6368",
  padding: 6,
  borderRadius: 8,
  cursor: "pointer",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  lineHeight: 1,
};

const primaryIconBtnStyle: React.CSSProperties = {
  ...iconBtnStyle,
  background: "#1a73e8",
  borderColor: "#1a73e8",
  color: "#fff",
};


const primaryBtnStyle: React.CSSProperties = {
  ...smallBtnStyle,
  background: "#1a73e8",
  borderColor: "#1a73e8",
  color: "#fff",
};

const collapsedMessageStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  border: "1px solid #eee",
  borderRadius: 6,
  padding: "8px 12px",
  marginBottom: 4,
  background: "#f8f9fa",
  cursor: "pointer",
  color: "#202124",
};

const collapsedReplyBtnStyle: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "#666",
  cursor: "pointer",
  padding: 4,
  borderRadius: 4,
  display: "inline-flex",
  alignItems: "center",
  flexShrink: 0,
};

const messageStyle: React.CSSProperties = {
  border: "1px solid #eee",
  borderRadius: 6,
  padding: 12,
  marginBottom: 10,
  background: "#fafbfc",
  color: "#202124",
};

const messageHeaderStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  marginBottom: 8,
  fontSize: 13,
};

const messageActionsStyle: React.CSSProperties = {
  display: "flex",
  gap: 8,
  marginTop: 10,
  paddingTop: 8,
  borderTop: "1px solid #eee",
};

const messageActionBtnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  background: "#fff",
  border: "1px solid #dadce0",
  borderRadius: 16,
  padding: "5px 14px",
  fontSize: 12,
  color: "#444",
  cursor: "pointer",
};

const bodyTextStyle: React.CSSProperties = {
  whiteSpace: "pre-wrap",
  fontFamily: "inherit",
  margin: 0,
  fontSize: 13,
  color: "#222",
};

const bodyHtmlStyle: React.CSSProperties = {
  fontSize: 13,
  color: "#222",
  overflowX: "auto",
  lineHeight: 1.5,
  wordBreak: "break-word",
};

const quoteToggleStyle: React.CSSProperties = {
  display: "block",
  background: "#f1f3f4",
  border: "none",
  borderRadius: 4,
  padding: "2px 12px",
  fontSize: 14,
  color: "#5f6368",
  cursor: "pointer",
  marginTop: 6,
  letterSpacing: 2,
  fontWeight: 700,
  lineHeight: 1,
};

const draftCardStyle: React.CSSProperties = {
  border: "2px dashed #c5221f",
  borderRadius: 6,
  padding: 12,
  marginBottom: 10,
  background: "#fef7f6",
  color: "#202124",
};

const draftBodyStyle: React.CSSProperties = {
  whiteSpace: "pre-wrap",
  fontFamily: "inherit",
  margin: 0,
  fontSize: 13,
  color: "#444",
};

const imageAttachmentStyle: React.CSSProperties = {
  background: "#f8f9fa",
  border: "1px solid #e0e0e0",
  borderRadius: 8,
  padding: "8px 12px",
  minWidth: 120,
};

const attachmentsRowStyle: React.CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 6,
  marginTop: 10,
};

const attachmentChipStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  background: "#f1f3f4",
  border: "1px solid #dadce0",
  borderRadius: 16,
  padding: "4px 10px",
  fontSize: 12,
  color: "#3c4043",
};

const mutedStyle: React.CSSProperties = {
  color: "#777",
  fontSize: 13,
  padding: 8,
};

const statusStyle: React.CSSProperties = {
  marginTop: 12,
  padding: "6px 10px",
  background: "#e6f4ea",
  border: "1px solid #b6e0c2",
  borderRadius: 6,
  color: "#137333",
  fontSize: 12,
};

const errorStyle: React.CSSProperties = {
  marginTop: 12,
  padding: "6px 10px",
  background: "#fce8e6",
  border: "1px solid #f5c6c2",
  borderRadius: 6,
  color: "#b3261e",
  fontSize: 12,
};

// --------- Inline Composer styles ---------

const composerBackBtnStyle: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "#5f6368",
  cursor: "pointer",
  fontSize: 13,
  padding: "8px 0",
  marginBottom: 4,
  fontFamily: "'Google Sans', Roboto, Arial, sans-serif",
};

const composerSubjectStyle: React.CSSProperties = {
  fontSize: 22,
  fontWeight: 400,
  color: "#202124",
  margin: "0 0 16px 0",
  lineHeight: 1.3,
  fontFamily: "'Google Sans', Roboto, Arial, sans-serif",
};

const composerThreadPanelStyle: React.CSSProperties = {
  marginBottom: 8,
};

const composerCardStyle: React.CSSProperties = {
  borderRadius: 8,
  border: "1px solid #dadce0",
  boxShadow: "0 1px 2px 0 rgba(60,64,67,0.3), 0 1px 3px 1px rgba(60,64,67,0.15)",
  background: "#fff",
  overflow: "hidden",
  marginTop: 8,
};

const composerFieldRow: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  padding: "8px 16px",
  borderBottom: "1px solid #eceff1",
  gap: 0,
};

const composerFieldLabel: React.CSSProperties = {
  width: 36,
  color: "#5f6368",
  fontSize: 14,
  flexShrink: 0,
  fontFamily: "'Google Sans', Roboto, Arial, sans-serif",
};

const composerInputStyle: React.CSSProperties = {
  flex: 1,
  padding: "4px 0",
  border: "none",
  outline: "none",
  fontSize: 14,
  color: "#202124",
  background: "transparent",
  fontFamily: "Roboto, Arial, sans-serif",
};

const composerCcBccToggle: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "#5f6368",
  fontSize: 13,
  cursor: "pointer",
  flexShrink: 0,
  padding: "2px 4px",
  fontFamily: "'Google Sans', Roboto, Arial, sans-serif",
};

const composerFieldDivider: React.CSSProperties = {
  borderBottom: "1px solid #dadce0",
};

const composerTextareaStyle: React.CSSProperties = {
  width: "100%",
  minHeight: 200,
  padding: "12px 16px",
  border: "none",
  outline: "none",
  fontSize: 14,
  lineHeight: "20px",
  color: "#202124",
  fontFamily: "Arial, Helvetica, sans-serif",
  boxSizing: "border-box",
  resize: "vertical",
  background: "transparent",
};

const composerToolbarStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  padding: "4px 12px 8px 12px",
  borderTop: "1px solid #dadce0",
};

const composerToolbarLeft: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 4,
};

const composerToolbarRight: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const composerSendBtnStyle: React.CSSProperties = {
  background: "#0b57d0",
  color: "#fff",
  border: "none",
  padding: "8px 24px",
  borderRadius: 18,
  cursor: "pointer",
  fontSize: 14,
  fontWeight: 500,
  fontFamily: "'Google Sans', Roboto, sans-serif",
  lineHeight: "20px",
  minHeight: 36,
};

const composerToolbarIconBtn: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "#5f6368",
  cursor: "pointer",
  padding: 6,
  borderRadius: "50%",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
};

const composerTrashBtn: React.CSSProperties = {
  background: "none",
  border: "none",
  cursor: "pointer",
  padding: 6,
  borderRadius: "50%",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  transition: "color 0.15s, background 0.15s",
};

const composerSentStyle: React.CSSProperties = {
  background: "#e6f4ea",
  padding: "12px 16px",
  borderRadius: 8,
  color: "#137333",
  textAlign: "center",
  fontSize: 14,
  fontWeight: 500,
  fontFamily: "'Google Sans', Roboto, Arial, sans-serif",
};

function composerSaveStatusStyle(s: ComposerSaveStatus): React.CSSProperties {
  const base: React.CSSProperties = { fontSize: 11, fontFamily: "Roboto, Arial, sans-serif" };
  if (s.kind === "error") return { ...base, color: "#d93025" };
  if (s.kind === "saved") return { ...base, color: "#188038" };
  return { ...base, color: "#5f6368" };
}

const composerAgentBanner: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  padding: "8px 12px",
  background: "#fef7e0",
  border: "1px solid #fdd663",
  borderRadius: 8,
  fontSize: 13,
  color: "#3c4043",
  marginBottom: 8,
  fontFamily: "'Google Sans', Roboto, Arial, sans-serif",
};

const composerAgentApplyBtn: React.CSSProperties = {
  background: "#1a73e8",
  color: "#fff",
  border: "none",
  borderRadius: 14,
  padding: "4px 14px",
  fontSize: 12,
  fontWeight: 500,
  cursor: "pointer",
  fontFamily: "'Google Sans', Roboto, sans-serif",
};

const composerAgentKeepBtn: React.CSSProperties = {
  background: "none",
  color: "#5f6368",
  border: "1px solid #dadce0",
  borderRadius: 14,
  padding: "4px 14px",
  fontSize: 12,
  fontWeight: 500,
  cursor: "pointer",
  fontFamily: "'Google Sans', Roboto, sans-serif",
};

const attachmentRemoveBtn: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "#5f6368",
  cursor: "pointer",
  marginLeft: 4,
  padding: "0 2px",
  fontSize: 14,
  lineHeight: 1,
  borderRadius: "50%",
};

const previewOverlayStyle: React.CSSProperties = {
  position: "fixed",
  top: 0,
  left: 0,
  right: 0,
  bottom: 0,
  background: "rgba(0,0,0,0.5)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 1000,
};

const previewModalStyle: React.CSSProperties = {
  background: "#fff",
  borderRadius: 12,
  width: "90%",
  maxWidth: 800,
  height: "80%",
  maxHeight: 600,
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
  boxShadow: "0 8px 32px rgba(0,0,0,0.24)",
};

const previewHeaderStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "12px 16px",
  borderBottom: "1px solid #e0e0e0",
  fontFamily: "'Google Sans', Roboto, Arial, sans-serif",
};

const previewCloseBtn: React.CSSProperties = {
  background: "none",
  border: "none",
  fontSize: 20,
  cursor: "pointer",
  color: "#5f6368",
  padding: "4px 8px",
  borderRadius: "50%",
  lineHeight: 1,
};

const previewBodyStyle: React.CSSProperties = {
  flex: 1,
  overflow: "auto",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "#f8f9fa",
};

const composerConfirmDiscardRow: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  fontSize: 12,
  color: "#5f6368",
  fontFamily: "'Google Sans', Roboto, sans-serif",
};

const composerConfirmYesBtn: React.CSSProperties = {
  background: "#d93025",
  color: "#fff",
  border: "none",
  borderRadius: 12,
  padding: "3px 12px",
  fontSize: 11,
  fontWeight: 500,
  cursor: "pointer",
};

const composerConfirmNoBtn: React.CSSProperties = {
  background: "none",
  color: "#5f6368",
  border: "1px solid #dadce0",
  borderRadius: 12,
  padding: "3px 12px",
  fontSize: 11,
  fontWeight: 500,
  cursor: "pointer",
};

const composerCollapsedMsgStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  padding: "8px 12px",
  borderBottom: "1px solid #eceff1",
  cursor: "pointer",
  background: "#fff",
  borderRadius: 0,
};

const composerExpandedMsgStyle: React.CSSProperties = {
  padding: "12px 16px",
  borderBottom: "1px solid #eceff1",
  background: "#fff",
};

const composerBodyHtmlStyle: React.CSSProperties = {
  fontSize: 14,
  color: "#202124",
  lineHeight: 1.6,
  overflowX: "auto",
  wordBreak: "break-word",
};

const composerBodyTextStyle: React.CSSProperties = {
  whiteSpace: "pre-wrap",
  fontFamily: "Arial, Helvetica, sans-serif",
  margin: 0,
  fontSize: 14,
  color: "#202124",
  lineHeight: 1.6,
};

const composerQuoteToggle: React.CSSProperties = {
  display: "inline-block",
  background: "#f1f3f4",
  border: "none",
  borderRadius: 4,
  padding: "2px 10px",
  fontSize: 13,
  color: "#5f6368",
  cursor: "pointer",
  marginTop: 6,
  letterSpacing: 2,
  fontWeight: 700,
  lineHeight: 1,
};
