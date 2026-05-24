import { useEffect, useRef, useState } from "react";
import {
  ArrowCounterClockwise,
  EnvelopeOpen,
  Archive,
  ArrowBendUpLeft,
  CheckCircle,
} from "@phosphor-icons/react";

export type CuratedThread = {
  thread_id: string;
  subject?: string;
  from?: string;
  snippet?: string;
  last_message_at?: string;
  importance_score: number;
  reasons: string[];
};

export type Attachment = {
  filename?: string;
  mime_type?: string;
  size?: number;
  attachment_id?: string;
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

export type Thread = {
  thread_id: string;
  messages: ThreadMessage[];
};

export type CurateResult = { threads: CuratedThread[] };

type McpAppLike = {
  ontoolresult?: (result: unknown) => void;
  callServerTool: (args: {
    name: string;
    arguments: Record<string, unknown>;
  }) => Promise<unknown>;
};

type InboxProps = { mcpApp: McpAppLike };

export function Inbox({ mcpApp }: InboxProps) {
  const [threads, setThreads] = useState<CuratedThread[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [thread, setThread] = useState<Thread | null>(null);
  const [loadingThread, setLoadingThread] = useState(false);
  const [unreadRemoved, setUnreadRemoved] = useState<Set<string>>(new Set());
  const [markingDone, setMarkingDone] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Monotonic id for openThread; only the most-recent request may mutate state.
  const openSeqRef = useRef(0);

  useEffect(() => {
    let received = false;
    const handler = (raw: unknown) => {
      const data = extractStructuredContent<CurateResult>(raw);
      if (data && Array.isArray(data.threads)) {
        received = true;
        setThreads(data.threads);
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
      // A newer click happened while we were awaiting; ignore the stale result.
      if (seq !== openSeqRef.current) return;
      const data = extractStructuredContent<Thread>(raw);
      if (data && Array.isArray(data.messages)) setThread(data);
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
    try {
      await mcpApp.callServerTool({
        name: "gmail_inbox.archive",
        arguments: { thread_id: removingId },
      });
    } catch (err) {
      setError(errMsg(err));
    }
  };

  const reply = async () => {
    if (!selectedId) return;
    setStatus(null);
    try {
      await mcpApp.callServerTool({
        name: "gmail_inbox.reply",
        arguments: { thread_id: selectedId },
      });
      setStatus("Reply draft created - switch to composer");
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

  const visibleThreads = threads;

  return (
    <div style={appStyle}>
      <aside style={listPaneStyle}>
        <header style={listHeaderStyle}>
          <strong style={{ fontSize: 14 }}>Curated inbox</strong>
          <button onClick={refresh} style={iconBtnStyle} title="Refresh">
            <ArrowCounterClockwise size={16} />
          </button>
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
                  onClick={() => openThread(t.thread_id)}
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
                        <button
                          onClick={(e) => { e.stopPropagation(); markDone(t.thread_id); }}
                          style={dismissRowBtnStyle}
                          title="Mark done"
                        >
                          <CheckCircle size={14} />
                        </button>
                        <span style={chipStyle} title={t.reasons.join(", ")}>
                          {t.importance_score.toFixed(2)}
                        </span>
                      </div>
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
        {selectedId === null ? (
          <div style={mutedStyle}>Select a thread on the left.</div>
        ) : loadingThread ? (
          <div style={mutedStyle}>Loading thread…</div>
        ) : thread === null ? (
          <div style={mutedStyle}>(empty)</div>
        ) : (
          <ThreadReader
            thread={thread}
            onRefresh={refresh}
            onMarkRead={markRead}
            onArchive={archive}
            onDismiss={() => markDone()}
            onReply={reply}
          />
        )}
        {status && <div style={statusStyle}>{status}</div>}
        {error && (
          <div role="alert" style={errorStyle}>
            {error}
          </div>
        )}
      </main>
    </div>
  );
}

function ThreadReader({
  thread,
  onRefresh,
  onMarkRead,
  onArchive,
  onDismiss,
  onReply,
}: {
  thread: Thread;
  onRefresh: () => void;
  onMarkRead: () => void;
  onArchive: () => void;
  onDismiss: () => void;
  onReply: () => void;
}) {
  const first = thread.messages[0];
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
        <button onClick={onDismiss} style={iconBtnStyle} title="Mark done">
          <CheckCircle size={16} />
        </button>
        <button onClick={onReply} style={primaryIconBtnStyle} title="Reply">
          <ArrowBendUpLeft size={16} weight="bold" />
        </button>
      </div>
      <h3 style={{ margin: "8px 0 4px 0" }}>{subject}</h3>
      <div style={mutedStyle}>
        {thread.messages.length} message{thread.messages.length === 1 ? "" : "s"}
      </div>
      <div style={{ marginTop: 12 }}>
        {thread.messages.map((m) => (
          <MessageView key={m.message_id} message={m} />
        ))}
      </div>
    </>
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

function MessageView({ message }: { message: ThreadMessage }) {
  const imageAttachments = message.attachments.filter(
    (a) => a.mime_type?.startsWith("image/")
  );
  const otherAttachments = message.attachments.filter(
    (a) => !a.mime_type?.startsWith("image/")
  );

  return (
    <article style={messageStyle} data-testid={`msg-${message.message_id}`}>
      <header style={messageHeaderStyle}>
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
        <div style={{ color: "#666", fontSize: 12, flexShrink: 0 }}>
          {formatDate(message.date)}
        </div>
      </header>
      <MessageBody message={message} />
      {imageAttachments.length > 0 && (
        <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 8 }}>
          {imageAttachments.map((a, i) => (
            <div
              key={a.attachment_id || `${message.message_id}-img-${i}`}
              style={imageAttachmentStyle}
              data-testid="image-attachment"
            >
              <div style={{ fontSize: 11, color: "#555", marginBottom: 4 }}>
                {a.filename || "image"}
              </div>
              <div style={{ fontSize: 11, color: "#999" }}>
                {a.mime_type}{typeof a.size === "number" && ` · ${formatSize(a.size)}`}
              </div>
            </div>
          ))}
        </div>
      )}
      {otherAttachments.length > 0 && (
        <div style={attachmentsRowStyle}>
          {otherAttachments.map((a, i) => (
            <span
              key={a.attachment_id || `${message.message_id}-att-${i}`}
              style={attachmentChipStyle}
              data-testid="attachment-chip"
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
    </article>
  );
}

function MessageBody({ message }: { message: ThreadMessage }) {
  if (message.body_html) {
    return (
      <div
        style={bodyHtmlStyle}
        dangerouslySetInnerHTML={{ __html: message.body_html }}
      />
    );
  }
  if (message.body_text) {
    return <pre style={bodyTextStyle}>{message.body_text}</pre>;
  }
  return <div style={mutedStyle}>(no body)</div>;
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

const dismissRowBtnStyle: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "#aaa",
  padding: 2,
  cursor: "pointer",
  display: "inline-flex",
  alignItems: "center",
  flexShrink: 0,
  borderRadius: 4,
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
  background: "#fff",
  border: "1px solid #dadce0",
  borderRadius: 14,
  padding: "3px 10px",
  fontSize: 12,
  color: "#333",
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
