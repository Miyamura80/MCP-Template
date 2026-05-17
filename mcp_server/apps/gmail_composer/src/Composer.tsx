import { useEffect, useRef, useState } from "react";

export type Draft = {
  draft_id: string;
  from?: string;
  to?: string;
  cc?: string;
  bcc?: string;
  subject?: string;
  body?: string;
  thread_id?: string;
};

type McpAppLike = {
  ontoolresult?: (result: unknown) => void;
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
  };
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

export function Composer({ mcpApp }: ComposerProps) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [showCcBcc, setShowCcBcc] = useState(false);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>({ kind: "idle" });
  const [sent, setSent] = useState<SentState | null>(null);
  const [discarded, setDiscarded] = useState(false);
  const [confirmingDiscard, setConfirmingDiscard] = useState(false);
  const [pendingAgent, setPendingAgent] = useState<Draft | null>(null);
  const localDirtyRef = useRef(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const draftRef = useRef<Draft | null>(null);

  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  useEffect(() => {
    const handler = (raw: unknown) => {
      const incoming = extractDraft(raw);
      if (!incoming) return;
      const current = draftRef.current;
      if (!current) {
        setDraft(incoming);
        localDirtyRef.current = false;
        return;
      }
      if (localDirtyRef.current && !fieldsEqual(current, incoming)) {
        setPendingAgent(incoming);
        return;
      }
      setDraft(incoming);
      localDirtyRef.current = false;
    };
    mcpApp.ontoolresult = handler;
    return () => {
      if (mcpApp.ontoolresult === handler) {
        mcpApp.ontoolresult = undefined;
      }
    };
  }, [mcpApp]);

  const scheduleAutoSave = (next: Draft) => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      void persistDraft(next);
    }, 800);
  };

  const persistDraft = async (next: Draft) => {
    setSaveStatus({ kind: "saving" });
    try {
      await mcpApp.callServerTool({
        name: "gmail_composer.save_draft",
        arguments: {
          draft_id: next.draft_id,
          to: next.to ?? "",
          cc: next.cc ?? "",
          bcc: next.bcc ?? "",
          subject: next.subject ?? "",
          body: next.body ?? "",
        },
      });
      setSaveStatus({ kind: "saved", at: new Date() });
      localDirtyRef.current = false;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setSaveStatus({ kind: "error", message: msg });
    }
  };

  const updateField = (key: keyof Draft, value: string) => {
    if (!draft) return;
    const next: Draft = { ...draft, [key]: value };
    setDraft(next);
    localDirtyRef.current = true;
    scheduleAutoSave(next);
  };

  const onSaveNow = () => {
    if (!draft) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    void persistDraft(draft);
  };

  const onSend = async () => {
    if (!draft) return;
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    try {
      const raw = await mcpApp.callServerTool({
        name: "gmail_composer.send",
        arguments: {
          draft_id: draft.draft_id,
          to: draft.to ?? "",
          cc: draft.cc ?? "",
          bcc: draft.bcc ?? "",
          subject: draft.subject ?? "",
          body: draft.body ?? "",
        },
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
    setDraft(pendingAgent);
    setPendingAgent(null);
    localDirtyRef.current = false;
  };

  const keepLocal = () => setPendingAgent(null);

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
        value={draft.body ?? ""}
        onChange={(e) => updateField("body", e.target.value)}
        rows={14}
        style={textareaStyle}
        aria-label="Body"
      />

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

function statusStyle(s: SaveStatus): React.CSSProperties {
  if (s.kind === "error") return { color: "#991b1b", fontSize: 12 };
  if (s.kind === "saved") return { color: "#059669", fontSize: 12 };
  return { color: "#666", fontSize: 12 };
}
