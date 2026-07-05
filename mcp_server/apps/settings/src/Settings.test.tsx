import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Settings, type McpAppLike, type Snapshot } from "./Settings";

function snap(over: Partial<Snapshot> = {}): Snapshot {
  return {
    gmail_connected: true,
    gmail_email: "me@example.com",
    watching: true,
    watch_expiration: null,
    push_available: true,
    subscriptions: [],
    ...over,
  };
}

function makeApp(
  calls: { name: string; args: Record<string, unknown> }[],
  getSnap: Snapshot = snap()
) {
  const responses: Record<string, unknown> = {
    "settings.get": { structuredContent: getSnap },
    "settings.subscribe": {
      structuredContent: { id: "s1", secret: "whsec_abc123" },
    },
    "settings.unsubscribe": { structuredContent: { unsubscribed: true } },
    "settings.rotate_secret": {
      structuredContent: { id: "s1", secret: "whsec_new999" },
    },
  };
  const app: McpAppLike = {
    callServerTool: vi.fn(async ({ name, arguments: args }) => {
      calls.push({ name, args });
      return responses[name];
    }),
  };
  return app;
}

describe("Settings panel", () => {
  it("renders the gmail snapshot from ontoolresult", async () => {
    const app = makeApp([]);
    render(<Settings mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: snap() });
    expect(await screen.findByText("me@example.com")).toBeInTheDocument();
    expect(screen.getByText("Watching inbox")).toBeInTheDocument();
  });

  it("adds an endpoint and reveals the one-time secret", async () => {
    const calls: { name: string; args: Record<string, unknown> }[] = [];
    const app = makeApp(calls);
    render(<Settings mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: snap() });

    fireEvent.change(await screen.findByLabelText("Webhook endpoint URL"), {
      target: { value: "https://hooks.example.com/gmail" },
    });
    fireEvent.click(screen.getByText("Add endpoint"));

    expect(await screen.findByText("whsec_abc123")).toBeInTheDocument();
    const sub = calls.find((c) => c.name === "settings.subscribe");
    expect(sub?.args.url).toBe("https://hooks.example.com/gmail");
  });

  it("explains when push is unavailable", async () => {
    const app = makeApp([], snap({ push_available: false }));
    render(<Settings mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: snap({ push_available: false }) });
    await waitFor(() =>
      expect(screen.getByText(/aren't enabled on this server/i)).toBeInTheDocument()
    );
    expect(screen.queryByText("Add endpoint")).not.toBeInTheDocument();
  });
});
