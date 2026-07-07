import { useEffect, useRef, useState } from "react";
import {
  ArrowCounterClockwise,
  EnvelopeOpen,
  Archive,
  Sparkle,
} from "@phosphor-icons/react";
import type { Draft, McpAppLike, Thread, ThreadMessage } from "./types";
import { base64ToBlobUrl, extractStructuredContent, isPreviewable } from "./model";
import { MarkDoneButton, PdfViewer } from "./shared";
import { CollapsibleMessage, DraftCard } from "./MessageView";
import { actionsStyle, iconBtnStyle, mutedStyle, replyContextInputStyle } from "./styles";
import {
  previewBodyStyle,
  previewCloseBtn,
  previewHeaderStyle,
  previewModalStyle,
  previewOverlayStyle,
} from "./composerStyles";

export function ThreadReader({
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
