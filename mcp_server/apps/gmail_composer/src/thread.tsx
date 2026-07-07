import { useState } from "react";
import { sanitizeHtml } from "./sanitize";
import type { Thread, ThreadMessage } from "./draftModel";
import {
  quoteToggleBtnStyle,
  threadBodyHtmlStyle,
  threadBodyTextStyle,
  threadMsgCollapsedStyle,
  threadMsgExpandedStyle,
  threadPanelStyle,
  threadToggleBtn,
} from "./styles";

export function extractThread(raw: unknown): Thread | null {
  if (!raw || typeof raw !== "object") return null;
  const wrapper = raw as { structuredContent?: unknown };
  const data = (wrapper.structuredContent ?? raw) as Record<string, unknown>;
  if (!data || typeof data !== "object") return null;
  if (!Array.isArray((data as { messages?: unknown }).messages)) return null;
  return data as unknown as Thread;
}

export function ThreadPanel({
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
