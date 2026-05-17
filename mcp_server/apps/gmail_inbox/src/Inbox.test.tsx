import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  Inbox,
  type CurateResult,
  type CuratedThread,
  type Thread,
} from "./Inbox";

function makeMcpApp(opts?: {
  threadResults?: Record<string, Thread>;
  refreshResult?: CurateResult;
}) {
  const calls: { name: string; arguments: Record<string, unknown> }[] = [];
  const callServerTool = vi.fn(
    async (args: { name: string; arguments: Record<string, unknown> }) => {
      calls.push(args);
      if (args.name === "gmail_inbox.open_thread") {
        const tid = args.arguments.thread_id as string;
        const t = opts?.threadResults?.[tid];
        return t ? { structuredContent: t } : null;
      }
      if (args.name === "gmail_inbox.refresh") {
        return opts?.refreshResult
          ? { structuredContent: opts.refreshResult }
          : { structuredContent: { threads: [] } };
      }
      return null;
    }
  );
  const app: {
    ontoolresult?: (raw: unknown) => void;
    callServerTool: typeof callServerTool;
  } = { callServerTool };
  return { app, callServerTool, calls };
}

const threadA: CuratedThread = {
  thread_id: "tA",
  subject: "VIP message",
  from: "ceo@example.com",
  snippet: "urgent...",
  last_message_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
  importance_score: 0.85,
  reasons: ["Marked IMPORTANT by Gmail", "Unread"],
};

const threadB: CuratedThread = {
  thread_id: "tB",
  subject: "Just a hello",
  from: "friend@example.com",
  snippet: "hey",
  last_message_at: new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString(),
  importance_score: 0.32,
  reasons: ["Recent (~72h old)"],
};

const sampleResult: CurateResult = { threads: [threadA, threadB] };

const plainThread: Thread = {
  thread_id: "tA",
  messages: [
    {
      message_id: "m1",
      from: "ceo@example.com",
      to: "me@example.com",
      date: new Date().toISOString(),
      subject: "VIP message",
      body_text: "Hello world plain body",
      attachments: [
        {
          filename: "doc.pdf",
          mime_type: "application/pdf",
          size: 1024,
          attachment_id: "att-1",
        },
      ],
    },
  ],
};

const htmlThread: Thread = {
  thread_id: "tB",
  messages: [
    {
      message_id: "m2",
      from: "friend@example.com",
      to: "me@example.com",
      date: new Date().toISOString(),
      subject: "Just a hello",
      body_html: "<p data-testid='html-body'>html body here</p>",
      attachments: [],
    },
  ],
};

describe("Inbox", () => {
  it("renders empty state before any tool result", () => {
    const { app } = makeMcpApp();
    render(<Inbox mcpApp={app} />);
    expect(screen.getByText(/loading inbox/i)).toBeInTheDocument();
    expect(screen.getByText(/select a thread/i)).toBeInTheDocument();
  });

  it("renders curated threads once ontoolresult fires", async () => {
    const { app } = makeMcpApp();
    render(<Inbox mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: sampleResult });
    expect(await screen.findByText("VIP message")).toBeInTheDocument();
    expect(screen.getByText("Just a hello")).toBeInTheDocument();
  });

  it("clicking a row calls gmail_inbox.open_thread and renders the thread", async () => {
    const { app, calls } = makeMcpApp({
      threadResults: { tA: plainThread },
    });
    render(<Inbox mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: sampleResult });
    const row = await screen.findByTestId("row-tA");
    fireEvent.click(row);
    await waitFor(() => {
      expect(
        calls.some(
          (c) =>
            c.name === "gmail_inbox.open_thread" &&
            c.arguments.thread_id === "tA"
        )
      ).toBe(true);
    });
    expect(await screen.findByTestId("msg-m1")).toBeInTheDocument();
    expect(screen.getByText(/Hello world plain body/)).toBeInTheDocument();
  });

  it("renders HTML body when no plaintext is provided", async () => {
    const { app } = makeMcpApp({
      threadResults: { tB: htmlThread },
    });
    render(<Inbox mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: sampleResult });
    const row = await screen.findByTestId("row-tB");
    fireEvent.click(row);
    expect(await screen.findByTestId("html-body")).toBeInTheDocument();
  });

  it("renders attachment chips in the message", async () => {
    const { app } = makeMcpApp({
      threadResults: { tA: plainThread },
    });
    render(<Inbox mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: sampleResult });
    fireEvent.click(await screen.findByTestId("row-tA"));
    const chips = await screen.findAllByTestId("attachment-chip");
    expect(chips).toHaveLength(1);
    expect(chips[0]).toHaveTextContent(/doc\.pdf/);
  });

  it("mark-read optimistically de-bolds the row by clearing the unread chip", async () => {
    const { app, calls } = makeMcpApp({
      threadResults: { tA: plainThread },
    });
    render(<Inbox mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: sampleResult });
    const row = await screen.findByTestId("row-tA");
    fireEvent.click(row);
    await screen.findByTestId("msg-m1");
    const subjectInRow = row.querySelector("span") as HTMLElement;
    expect(subjectInRow).toHaveStyle({ fontWeight: "700" });
    const markBtn = screen.getByRole("button", { name: /mark read/i });
    fireEvent.click(markBtn);
    await waitFor(() => {
      expect(
        calls.some((c) => c.name === "gmail_inbox.mark_read")
      ).toBe(true);
    });
    await waitFor(() => {
      const updatedRow = screen.getByTestId("row-tA");
      const span = updatedRow.querySelector("span") as HTMLElement;
      expect(span).toHaveStyle({ fontWeight: "500" });
    });
  });

  it("archive removes the row from the list optimistically", async () => {
    const { app, calls } = makeMcpApp({
      threadResults: { tA: plainThread },
    });
    render(<Inbox mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: sampleResult });
    fireEvent.click(await screen.findByTestId("row-tA"));
    await screen.findByTestId("msg-m1");
    fireEvent.click(screen.getByRole("button", { name: /archive/i }));
    await waitFor(() => {
      expect(screen.queryByTestId("row-tA")).not.toBeInTheDocument();
    });
    expect(calls.some((c) => c.name === "gmail_inbox.archive")).toBe(true);
    expect(screen.getByText(/select a thread/i)).toBeInTheDocument();
  });

  it("reply triggers gmail_inbox.reply and shows a transient status", async () => {
    const { app, calls } = makeMcpApp({
      threadResults: { tA: plainThread },
    });
    render(<Inbox mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: sampleResult });
    fireEvent.click(await screen.findByTestId("row-tA"));
    await screen.findByTestId("msg-m1");
    fireEvent.click(screen.getByRole("button", { name: /reply/i }));
    await waitFor(() => {
      expect(calls.some((c) => c.name === "gmail_inbox.reply")).toBe(true);
    });
    expect(
      await screen.findByText(/reply draft created/i)
    ).toBeInTheDocument();
  });

  it("clears the ontoolresult handler on unmount", () => {
    const { app } = makeMcpApp();
    const { unmount } = render(<Inbox mcpApp={app} />);
    expect(app.ontoolresult).toBeDefined();
    unmount();
    expect(app.ontoolresult).toBeUndefined();
  });
});
