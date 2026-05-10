import { useEffect, useState } from "react";

type CheckStatus = "pass" | "fail" | "warn";

export type Check = {
  name: string;
  status: CheckStatus;
  message: string;
  detail?: string;
  fixable?: boolean;
};

export type DoctorResult = {
  checks: Check[];
  has_failures: boolean;
};

type McpAppLike = {
  ontoolresult?: (result: unknown) => void;
  callServerTool: (args: { name: string; arguments: Record<string, unknown> }) => Promise<unknown>;
};

type DashboardProps = {
  mcpApp: McpAppLike;
};

const statusColor: Record<CheckStatus, string> = {
  pass: "#22c55e",
  fail: "#ef4444",
  warn: "#eab308",
};

export function Dashboard({ mcpApp }: DashboardProps) {
  const [result, setResult] = useState<DoctorResult | null>(null);
  const [fixing, setFixing] = useState(false);
  const [fixError, setFixError] = useState<string | null>(null);

  useEffect(() => {
    const handler = (raw: unknown) => {
      const data = extractDoctorResult(raw);
      if (data) setResult(data);
    };
    mcpApp.ontoolresult = handler;
    return () => {
      if (mcpApp.ontoolresult === handler) {
        mcpApp.ontoolresult = undefined;
      }
    };
  }, [mcpApp]);

  const onFix = async () => {
    setFixing(true);
    setFixError(null);
    try {
      const raw = await mcpApp.callServerTool({
        name: "doctor",
        arguments: { fix: true },
      });
      const data = extractDoctorResult(raw);
      if (data) setResult(data);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error("doctor auto-fix failed:", err);
      setFixError(msg);
    } finally {
      setFixing(false);
    }
  };

  if (!result) return <div style={containerStyle}>Loading checks…</div>;

  return (
    <div style={containerStyle}>
      <header style={{ marginBottom: 16, display: "flex", justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>Doctor</h2>
        {result.has_failures && (
          <button onClick={onFix} disabled={fixing} style={fixButtonStyle}>
            {fixing ? "Fixing…" : "Auto-Fix"}
          </button>
        )}
      </header>
      {fixError && (
        <div role="alert" style={errorStyle}>
          Auto-fix failed: {fixError}
        </div>
      )}
      <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
        {result.checks.map((c) => (
          <li key={c.name} style={rowStyle}>
            <span
              data-testid={`status-${c.name}`}
              style={{ ...dotStyle, background: statusColor[c.status] }}
            />
            <div style={{ flex: 1 }}>
              <strong>{c.name}</strong>
              <div style={{ color: "#666", fontSize: 13 }}>{c.message}</div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function extractDoctorResult(raw: unknown): DoctorResult | null {
  if (!raw || typeof raw !== "object") return null;
  const wrapper = raw as { structuredContent?: unknown };
  const data = wrapper.structuredContent ?? raw;
  if (data && typeof data === "object" && Array.isArray((data as DoctorResult).checks)) {
    return data as DoctorResult;
  }
  return null;
}

const containerStyle: React.CSSProperties = {
  fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif",
  padding: 16,
  maxWidth: 720,
};

const rowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: 12,
  padding: "8px 0",
  borderBottom: "1px solid #eee",
};

const dotStyle: React.CSSProperties = {
  display: "inline-block",
  width: 10,
  height: 10,
  borderRadius: "50%",
  marginTop: 6,
  flexShrink: 0,
};

const fixButtonStyle: React.CSSProperties = {
  background: "#3b82f6",
  color: "white",
  border: "none",
  padding: "6px 12px",
  borderRadius: 6,
  cursor: "pointer",
};

const errorStyle: React.CSSProperties = {
  background: "#fef2f2",
  border: "1px solid #fecaca",
  color: "#991b1b",
  padding: "8px 12px",
  borderRadius: 6,
  marginBottom: 12,
  fontSize: 13,
};
