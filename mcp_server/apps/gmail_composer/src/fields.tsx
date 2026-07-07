import type { Draft } from "./draftModel";
import {
  inputStyle,
  labelStyle,
  linkButtonStyle,
  mobileTextareaStyle,
  readOnlyStyle,
  rowStyle,
  textareaStyle,
} from "./styles";

export function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={rowStyle}>
      <div style={labelStyle}>{label}</div>
      <div style={{ flex: 1 }}>{children}</div>
    </div>
  );
}

// The From/To/Cc/Bcc/Subject/Body inputs. Purely presentational: every edit is
// routed back through `updateField`, which owns the debounced autosave.
export function RecipientFields({
  draft,
  updateField,
  showCcBcc,
  setShowCcBcc,
  isMobile,
  bodyRef,
}: {
  draft: Draft;
  updateField: (key: keyof Draft, value: string) => void;
  showCcBcc: boolean;
  setShowCcBcc: (v: boolean) => void;
  isMobile: boolean;
  bodyRef: React.RefObject<HTMLTextAreaElement | null>;
}) {
  return (
    <>
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
    </>
  );
}
