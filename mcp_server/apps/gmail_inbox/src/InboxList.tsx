import { ArrowCounterClockwise } from "@phosphor-icons/react";
import type { Coverage, CuratedThread } from "./types";
import { relativeTime } from "./model";
import { MarkDoneButton, SenderAvatar } from "./shared";
import {
  chipStyle,
  draftChipStyle,
  draftDiscardBtnStyle,
  iconBtnStyle,
  labelChipBaseStyle,
  labelChipsRowStyle,
  listHeaderStyle,
  listPaneNarrowStyle,
  listPaneStyle,
  listStyle,
  listStyleNarrow,
  mutedStyle,
  reasonChipStyle,
  rowFootStyle,
  rowMidStyle,
  rowSnippetStyle,
  rowStyle,
  rowTopStyle,
} from "./styles";

export function InboxList({
  threads,
  coverage,
  selectedId,
  showScores,
  unreadRemoved,
  narrow,
  onToggleScores,
  onRefresh,
  onOpenThread,
  onMarkDone,
  onDiscardDraft,
}: {
  threads: CuratedThread[] | null;
  coverage: Coverage | null;
  selectedId: string | null;
  showScores: boolean;
  unreadRemoved: Set<string>;
  narrow: boolean;
  onToggleScores: () => void;
  onRefresh: () => void;
  onOpenThread: (thread_id: string) => void;
  onMarkDone: (thread_id: string) => void;
  onDiscardDraft: (threadId: string, draftId: string, e: React.MouseEvent) => void;
}) {
  const visibleThreads = threads;
  return (
    <aside style={narrow ? listPaneNarrowStyle : listPaneStyle}>
      <header style={listHeaderStyle}>
        <strong style={{ fontSize: 14 }}>Curated inbox</strong>
        <div style={{ display: "flex", gap: 4 }}>
          <button
            onClick={onToggleScores}
            style={{ ...iconBtnStyle, fontSize: 11, fontWeight: 600, width: 28, height: 28, color: showScores ? "#1a73e8" : "#5f6368", background: showScores ? "#e8f0fe" : "#fff" }}
            title={showScores ? "Hide scores" : "Show scores"}
          >
            #
          </button>
          <button onClick={onRefresh} style={iconBtnStyle} title="Refresh">
            <ArrowCounterClockwise size={16} />
          </button>
        </div>
      </header>
      {coverage && (
        <div
          data-testid="coverage-banner"
          style={{
            padding: "6px 12px",
            fontSize: 11,
            color: "#5f6368",
            background: "#f8f9fa",
            borderBottom: "1px solid #ebebeb",
            display: "flex",
            gap: 10,
            flexWrap: "wrap",
          }}
        >
          <span style={{ color: "#188038" }}>{coverage.curated} triaged</span>
          {coverage.stale > 0 && (
            <span style={{ color: "#b06000" }}>{coverage.stale} stale</span>
          )}
          {coverage.uncurated > 0 && (
            <span>{coverage.uncurated} not yet triaged</span>
          )}
        </div>
      )}
      {visibleThreads === null ? (
        <div style={mutedStyle}>Loading inbox…</div>
      ) : visibleThreads.length === 0 ? (
        <div style={mutedStyle}>No threads.</div>
      ) : (
        <ul style={narrow ? listStyleNarrow : listStyle}>
          {visibleThreads.map((t) => {
            const isSelected = t.thread_id === selectedId;
            const showUnread =
              t.reasons.some((r) => r.toLowerCase().includes("unread")) &&
              !unreadRemoved.has(t.thread_id);
            return (
              <li
                key={t.thread_id}
                onClick={() => onOpenThread(t.thread_id)}
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
                      <MarkDoneButton onClick={(e) => { e.stopPropagation(); onMarkDone(t.thread_id); }} size="row" />
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
                                onClick={(e) => onDiscardDraft(t.thread_id, t.draft_id!, e)}
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
  );
}
