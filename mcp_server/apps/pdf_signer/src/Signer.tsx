// pdf_signer MCP App - the human half of the signing ceremony.
//
// The host renders this iframe when pdf_request_signature locks a document.
// The user reviews every page (all pages render into one scrollable column,
// with the signature placement highlighted), types their full legal name,
// ticks the consent checkbox, and clicks Sign - which calls the app-only
// pdf_signer.sign tool. The model never sees this UI and cannot drive it;
// on elicitation-capable hosts the server additionally asks the host to
// show a native confirmation dialog before sealing.

import { useCallback, useEffect, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
import pdfjsWorkerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorkerSrc;

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

export type SignerDocument = {
  doc_id: string;
  filename: string;
  status: "open" | "awaiting_signature" | "signed";
  page_count: number;
  stamp_page?: number | null;
  // Exact stamp footprint [x0, y0, x1, y1] in PDF user space, resolved
  // server-side so the highlight and the real stamp share one geometry.
  stamp_rect?: number[] | null;
  data_base64: string;
};

type SignOutcome = {
  status?: string;
  signed_by?: string;
  signed_at_utc?: string;
  message?: string;
};

type Phase =
  | "waiting"
  | "loading"
  | "review"
  | "signing"
  | "signed"
  | "cancelled"
  | "error";

function unwrap(raw: unknown): Record<string, unknown> {
  const wrapper = (raw ?? {}) as { structuredContent?: unknown };
  return (wrapper.structuredContent ?? raw ?? {}) as Record<string, unknown>;
}

type RenderedPage = {
  pageNo: number;
  url: string;
  cssWidth: number;
  cssHeight: number;
  scale: number;
  pdfHeight: number;
};

const PAGE_TARGET_WIDTH = 640;

async function renderAllPages(dataBase64: string): Promise<RenderedPage[]> {
  const bytes = Uint8Array.from(atob(dataBase64), (c) => c.charCodeAt(0));
  const doc = await pdfjsLib.getDocument({ data: bytes, isEvalSupported: false })
    .promise;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const pages: RenderedPage[] = [];
  for (let pageNo = 1; pageNo <= doc.numPages; pageNo++) {
    const page = await doc.getPage(pageNo);
    const unscaled = page.getViewport({ scale: 1 });
    const scale = PAGE_TARGET_WIDTH / unscaled.width;
    const viewport = page.getViewport({ scale: scale * dpr });
    const canvas = document.createElement("canvas");
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) continue;
    await page.render({
      canvasContext: ctx,
      viewport,
      // Draw form-field values (filled by pdf_edit) even when the PDF only
      // carries /NeedAppearances instead of baked appearance streams.
      annotationMode: pdfjsLib.AnnotationMode.ENABLE_FORMS,
    }).promise;
    pages.push({
      pageNo,
      url: canvas.toDataURL("image/png"),
      cssWidth: unscaled.width * scale,
      cssHeight: unscaled.height * scale,
      scale,
      pdfHeight: unscaled.height,
    });
  }
  return pages;
}

export function Signer({ mcpApp }: { mcpApp: McpAppLike }) {
  const [phase, setPhase] = useState<Phase>("waiting");
  const [doc, setDoc] = useState<SignerDocument | null>(null);
  const [pages, setPages] = useState<RenderedPage[]>([]);
  const [typedName, setTypedName] = useState("");
  const [consent, setConsent] = useState(false);
  const [outcome, setOutcome] = useState<SignOutcome | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loadedDocId = useRef<string | null>(null);

  const loadDocument = useCallback(
    async (docId: string) => {
      setPhase("loading");
      setError(null);
      try {
        const raw = await mcpApp.callServerTool({
          name: "pdf_signer.get_document",
          arguments: { doc_id: docId },
        });
        const data = unwrap(raw) as SignerDocument;
        setDoc(data);
        loadedDocId.current = data.doc_id;
        setPages(await renderAllPages(data.data_base64));
        if (data.status === "signed") {
          setOutcome({ status: "signed", message: "This document is already signed." });
          setPhase("signed");
        } else {
          setPhase("review");
        }
      } catch (err) {
        setError(`Could not load the document: ${err}`);
        setPhase("error");
      }
    },
    [mcpApp]
  );

  // The pdf_request_signature tool result carries the doc_id to review.
  useEffect(() => {
    const handler = (result: unknown) => {
      const data = unwrap(result);
      const docId = typeof data.doc_id === "string" ? data.doc_id : null;
      if (docId && docId !== loadedDocId.current) {
        void loadDocument(docId);
      }
    };
    mcpApp.ontoolresult = handler;
    return () => {
      if (mcpApp.ontoolresult === handler) mcpApp.ontoolresult = undefined;
    };
  }, [mcpApp, loadDocument]);

  const canSign = typedName.trim().length > 0 && consent && phase === "review";

  const handleSign = async () => {
    if (!doc || !canSign) return;
    setPhase("signing");
    setError(null);
    try {
      const raw = await mcpApp.callServerTool({
        name: "pdf_signer.sign",
        arguments: {
          doc_id: doc.doc_id,
          typed_name: typedName.trim(),
          consent,
        },
      });
      const result = unwrap(raw) as SignOutcome;
      setOutcome(result);
      if (result.status === "signed") {
        setPhase("signed");
        try {
          await mcpApp.sendMessage?.({
            role: "user",
            content: [
              {
                type: "text",
                text: `I signed ${doc.filename} as "${result.signed_by}". The document is sealed - please continue.`,
              },
            ],
          });
        } catch {
          // sendMessage is best-effort; the signature itself already succeeded.
        }
      } else {
        // Host confirmation declined - the document is still awaiting.
        setPhase("review");
        setError(result.message ?? "Signing was not confirmed.");
      }
    } catch (err) {
      setPhase("review");
      setError(`Signing failed: ${err}`);
    }
  };

  const handleCancel = async () => {
    if (!doc) return;
    try {
      await mcpApp.callServerTool({
        name: "pdf_signer.cancel",
        arguments: { doc_id: doc.doc_id },
      });
      setPhase("cancelled");
    } catch (err) {
      setError(`Could not cancel: ${err}`);
    }
  };

  if (phase === "waiting") {
    return <div style={styles.centered}>Waiting for a document to sign…</div>;
  }
  if (phase === "loading") {
    return <div style={styles.centered}>Loading document…</div>;
  }
  if (phase === "error" && !doc) {
    return <div style={{ ...styles.centered, color: "#b3261e" }}>{error}</div>;
  }

  return (
    <div style={styles.shell}>
      <header style={styles.header}>
        <span style={styles.filename}>{doc?.filename}</span>
        {phase === "signed" && outcome ? (
          <span style={styles.signedBadge} data-testid="signed-badge">
            ✓ Signed{outcome.signed_by ? ` by ${outcome.signed_by}` : ""}
            {outcome.signed_at_utc ? ` · ${outcome.signed_at_utc}` : ""}
          </span>
        ) : phase === "cancelled" ? (
          <span style={styles.cancelledBadge}>Signing cancelled</span>
        ) : (
          <span style={styles.pendingBadge}>Awaiting your signature</span>
        )}
      </header>

      <div style={styles.pagesScroller} data-testid="pages">
        {pages.map((p) => (
          <div
            key={p.pageNo}
            style={{
              ...styles.pageWrap,
              width: p.cssWidth,
              height: p.cssHeight,
            }}
          >
            <img
              src={p.url}
              alt={`Page ${p.pageNo}`}
              width={p.cssWidth}
              height={p.cssHeight}
              style={{ display: "block" }}
            />
            {phase !== "signed" &&
              doc?.stamp_page === p.pageNo &&
              doc.stamp_rect != null &&
              doc.stamp_rect.length === 4 && (
                <div
                  data-testid="stamp-highlight"
                  style={{
                    ...styles.highlight,
                    left: doc.stamp_rect[0] * p.scale,
                    top: (p.pdfHeight - doc.stamp_rect[3]) * p.scale,
                    width: (doc.stamp_rect[2] - doc.stamp_rect[0]) * p.scale,
                    height: (doc.stamp_rect[3] - doc.stamp_rect[1]) * p.scale,
                  }}
                >
                  <span style={styles.highlightLabel}>Sign here</span>
                </div>
              )}
          </div>
        ))}
      </div>

      {(phase === "review" || phase === "signing") && (
        <footer style={styles.ceremony}>
          {error && (
            <div style={styles.errorBanner} role="alert">
              {error}
            </div>
          )}
          <label style={styles.fieldLabel} htmlFor="sign-as">
            Sign as (full legal name)
          </label>
          <input
            id="sign-as"
            type="text"
            value={typedName}
            onChange={(e) => setTypedName(e.target.value)}
            placeholder="Your full legal name"
            style={styles.nameInput}
            disabled={phase === "signing"}
          />
          <label style={styles.consentRow}>
            <input
              type="checkbox"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
              disabled={phase === "signing"}
            />
            <span>
              I agree that typing my name constitutes my electronic signature.
            </span>
          </label>
          <div style={styles.buttonRow}>
            <button
              onClick={handleCancel}
              style={styles.cancelBtn}
              disabled={phase === "signing"}
            >
              Cancel
            </button>
            <button
              onClick={handleSign}
              style={{
                ...styles.signBtn,
                opacity: canSign ? 1 : 0.5,
                cursor: canSign ? "pointer" : "not-allowed",
              }}
              disabled={!canSign}
            >
              {phase === "signing" ? "Signing…" : "Sign document"}
            </button>
          </div>
        </footer>
      )}
      {phase === "cancelled" && (
        <footer style={styles.ceremony}>
          <div>Signing cancelled - the document can be edited again.</div>
        </footer>
      )}
      {phase === "signed" && outcome?.message && (
        <footer style={styles.ceremony}>
          <div data-testid="signed-message">{outcome.message}</div>
        </footer>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  shell: {
    display: "flex",
    flexDirection: "column",
    height: "100vh",
    fontFamily: "system-ui, -apple-system, sans-serif",
    background: "#f5f5f5",
    color: "#1f1f1f",
  },
  centered: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    height: "100vh",
    color: "#5f6368",
    fontFamily: "system-ui, sans-serif",
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
    padding: "10px 16px",
    background: "#fff",
    borderBottom: "1px solid #e0e0e0",
    flexShrink: 0,
  },
  filename: { fontWeight: 600, fontSize: 14 },
  pendingBadge: {
    fontSize: 12,
    color: "#8a5a00",
    background: "#fff3d6",
    border: "1px solid #f0d48a",
    borderRadius: 999,
    padding: "3px 10px",
  },
  signedBadge: {
    fontSize: 12,
    color: "#0d652d",
    background: "#e6f4ea",
    border: "1px solid #b7dfc0",
    borderRadius: 999,
    padding: "3px 10px",
  },
  cancelledBadge: {
    fontSize: 12,
    color: "#5f6368",
    background: "#eee",
    borderRadius: 999,
    padding: "3px 10px",
  },
  pagesScroller: {
    flex: 1,
    overflow: "auto",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 16,
    padding: 16,
  },
  pageWrap: {
    position: "relative",
    background: "#fff",
    boxShadow: "0 1px 4px rgba(0,0,0,0.2)",
    flexShrink: 0,
  },
  highlight: {
    position: "absolute",
    border: "2px dashed #d97706",
    background: "rgba(251, 191, 36, 0.15)",
    borderRadius: 4,
    pointerEvents: "none",
  },
  highlightLabel: {
    position: "absolute",
    top: -20,
    left: 0,
    fontSize: 11,
    fontWeight: 600,
    color: "#92400e",
    background: "#fde68a",
    borderRadius: 3,
    padding: "1px 6px",
  },
  ceremony: {
    padding: "12px 16px",
    background: "#fff",
    borderTop: "1px solid #e0e0e0",
    display: "flex",
    flexDirection: "column",
    gap: 8,
    flexShrink: 0,
  },
  errorBanner: {
    background: "#fdecea",
    color: "#b3261e",
    border: "1px solid #f5c6c0",
    borderRadius: 6,
    padding: "6px 10px",
    fontSize: 13,
  },
  fieldLabel: { fontSize: 12, fontWeight: 600, color: "#444" },
  nameInput: {
    fontSize: 16,
    fontFamily: "'Segoe Script', 'Bradley Hand', cursive",
    padding: "8px 10px",
    border: "1px solid #dadce0",
    borderRadius: 6,
  },
  consentRow: {
    display: "flex",
    alignItems: "flex-start",
    gap: 8,
    fontSize: 13,
    color: "#333",
  },
  buttonRow: {
    display: "flex",
    justifyContent: "flex-end",
    gap: 8,
  },
  cancelBtn: {
    background: "none",
    border: "1px solid #dadce0",
    borderRadius: 6,
    padding: "8px 16px",
    fontSize: 14,
    cursor: "pointer",
  },
  signBtn: {
    background: "#0b57d0",
    color: "#fff",
    border: "none",
    borderRadius: 6,
    padding: "8px 20px",
    fontSize: 14,
    fontWeight: 600,
  },
};
