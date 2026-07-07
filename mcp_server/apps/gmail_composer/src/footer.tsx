import {
  buttonRowStyle,
  confirmRowStyle,
  destructiveButtonStyle,
  primaryButtonStyle,
  secondaryButtonStyle,
} from "./styles";

// The Send / Save draft / Discard action row, including the inline discard
// confirmation. Presentational: all handlers and the confirm flag live in
// Composer and are threaded in through props.
export function ComposerFooter({
  onSend,
  onSaveNow,
  confirmingDiscard,
  setConfirmingDiscard,
  onDiscardConfirm,
}: {
  onSend: () => void;
  onSaveNow: () => void;
  confirmingDiscard: boolean;
  setConfirmingDiscard: (v: boolean) => void;
  onDiscardConfirm: () => void;
}) {
  return (
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
  );
}
