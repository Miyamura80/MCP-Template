import { formatBytes, type Draft, type PendingUpload } from "./draftModel";
import {
  attachmentErrorItemStyle,
  attachmentIconStyle,
  attachmentItemStyle,
  attachmentListStyle,
  attachmentMetaStyle,
  attachmentNameStyle,
  attachmentRemoveStyle,
  dropHintStyle,
  dropZoneActiveStyle,
  dropZoneStyle,
  secondaryButtonStyle,
} from "./styles";

// The attach button + drag/drop zone + the list of persisted attachments and
// in-flight uploads. Presentational: all state and mutation logic lives in
// Composer and is threaded in through props.
export function AttachmentsSection({
  draft,
  uploads,
  dragActive,
  setDragActive,
  fileInputRef,
  onFilesChosen,
  onDrop,
  onRemoveAttachment,
  dismissUpload,
}: {
  draft: Draft;
  uploads: PendingUpload[];
  dragActive: boolean;
  setDragActive: (v: boolean) => void;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  onFilesChosen: (files: FileList | File[] | null) => void;
  onDrop: (e: React.DragEvent) => void;
  onRemoveAttachment: (attachmentId: string) => void;
  dismissUpload: (localId: string) => void;
}) {
  return (
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
  );
}
