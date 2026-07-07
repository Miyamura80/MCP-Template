import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle, CaretLeft, CaretRight } from "@phosphor-icons/react";
import * as pdfjsLib from "pdfjs-dist";
import pdfjsWorkerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { iconBtnStyle, pdfNavBtn } from "./styles";

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorkerSrc;

// Tracks whether the app is rendered in a narrow viewport (e.g. a phone-sized
// chat client). Drives the Gmail-mobile-style single-column navigation: the
// list and the reader occupy the full width and you navigate between them,
// instead of the cramped side-by-side two-pane layout used on wide screens.
export function useIsNarrow(breakpoint = 640): boolean {
  const query = `(max-width: ${breakpoint}px)`;
  const [narrow, setNarrow] = useState(() =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(query).matches
      : false,
  );
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(query);
    const handler = (e: MediaQueryListEvent) => setNarrow(e.matches);
    setNarrow(mq.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [query]);
  return narrow;
}

export function MarkDoneButton({ onClick, size = "row" }: { onClick: (e: React.MouseEvent) => void; size?: "row" | "action" }) {
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

export function SenderAvatar({ from }: { from: string | undefined }) {
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

export function PdfViewer({ url }: { url: string }) {
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
