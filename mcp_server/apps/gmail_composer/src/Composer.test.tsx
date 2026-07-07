import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, fireEvent } from "@testing-library/react";
import { Composer, type Draft, type Thread } from "./Composer";

function makeMcpApp(callResult: unknown = null) {
  const callServerTool = vi.fn(async () => callResult);
  const app = {
    ontoolresult: undefined as ((raw: unknown) => void) | undefined,
    callServerTool,
  };
  return { app, callServerTool };
}

// Installs a matchMedia stub so useIsMobile() resolves deterministically.
// jsdom ships no matchMedia, so tests without this helper exercise the
// desktop path (the graceful fallback in useIsMobile). Uses vi.stubGlobal so
// unstubAllGlobals restores the original absence without us hand-deleting a
// global we don't own.
function mockViewport(isMobile: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: isMobile,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const threadDraft: Draft = {
  draft_id: "d-thread",
  from: "alice@example.com",
  to: "bob@example.com",
  subject: "Re: Project",
  body: "Reply body",
  thread_id: "t-1",
};

const sampleThread: Thread = {
  thread_id: "t-1",
  messages: [
    { message_id: "m-1", from: "bob@example.com", body_text: "First message" },
    { message_id: "m-2", from: "alice@example.com", body_text: "Second message" },
  ],
};

const sampleDraft: Draft = {
  draft_id: "d-1",
  from: "alice@example.com",
  to: "bob@example.com",
  subject: "Hello",
  body: "Hi Bob",
};

describe("Composer", () => {
  it("renders empty state before ontoolresult", () => {
    const { app } = makeMcpApp();
    render(<Composer mcpApp={app} />);
    expect(screen.getByText(/waiting for draft/i)).toBeInTheDocument();
  });

  it("renders fields once ontoolresult fires", () => {
    const { app } = makeMcpApp();
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: sampleDraft });
    });
    expect(screen.getByLabelText("To")).toHaveValue("bob@example.com");
    expect(screen.getByLabelText("Subject")).toHaveValue("Hello");
    expect(screen.getByLabelText("Body")).toHaveValue("Hi Bob");
    expect(screen.getByText(/alice@example.com/i)).toBeInTheDocument();
  });

  it("debounces save_draft on field changes", async () => {
    vi.useFakeTimers();
    try {
      const { app, callServerTool } = makeMcpApp({ structuredContent: sampleDraft });
      render(<Composer mcpApp={app} />);
      act(() => {
        app.ontoolresult?.({ structuredContent: sampleDraft });
      });
      fireEvent.change(screen.getByLabelText("Subject"), {
        target: { value: "Updated" },
      });
      // Before 800ms passes, no save call yet.
      expect(callServerTool).not.toHaveBeenCalled();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(900);
      });
      expect(callServerTool).toHaveBeenCalledWith({
        name: "gmail_composer.save_draft",
        arguments: expect.objectContaining({
          draft_id: "d-1",
          subject: "Updated",
        }),
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("Send button calls gmail_composer.send with current field values", async () => {
    const sendResult = { structuredContent: { message_id: "msg-99" } };
    const { app, callServerTool } = makeMcpApp(sendResult);
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: sampleDraft });
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));
    await waitFor(() => {
      expect(callServerTool).toHaveBeenCalledWith({
        name: "gmail_composer.send",
        arguments: expect.objectContaining({
          draft_id: "d-1",
          to: "bob@example.com",
          subject: "Hello",
          body: "Hi Bob",
        }),
      });
    });
    expect(await screen.findByText(/msg-99/)).toBeInTheDocument();
  });

  it("Discard with confirm calls gmail_composer.discard", async () => {
    const { app, callServerTool } = makeMcpApp({ structuredContent: { discarded: true } });
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: sampleDraft });
    });
    fireEvent.click(screen.getByRole("button", { name: /^discard$/i }));
    fireEvent.click(screen.getByRole("button", { name: /yes, discard/i }));
    await waitFor(() => {
      expect(callServerTool).toHaveBeenCalledWith({
        name: "gmail_composer.discard",
        arguments: { draft_id: "d-1" },
      });
    });
    expect(await screen.findByText(/discarded\./i)).toBeInTheDocument();
  });

  it("auto-save failure renders an error indicator", async () => {
    vi.useFakeTimers();
    try {
      const { app } = makeMcpApp();
      app.callServerTool = vi.fn().mockRejectedValue(new Error("boom"));
      render(<Composer mcpApp={app} />);
      act(() => {
        app.ontoolresult?.({ structuredContent: sampleDraft });
      });
      fireEvent.change(screen.getByLabelText("Subject"), {
        target: { value: "x" },
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(900);
      });
      // Drain microtasks for the rejected promise to settle.
      await act(async () => {
        await vi.runOnlyPendingTimersAsync();
      });
      expect(screen.getByTestId("save-status").textContent).toMatch(/save failed/i);
    } finally {
      vi.useRealTimers();
    }
  });

  it("Show Cc/Bcc toggle reveals the two fields", () => {
    const { app } = makeMcpApp();
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: sampleDraft });
    });
    expect(screen.queryByLabelText("Cc")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /show cc\/bcc/i }));
    expect(screen.getByLabelText("Cc")).toBeInTheDocument();
    expect(screen.getByLabelText("Bcc")).toBeInTheDocument();
  });

  it("Incoming ontoolresult replaces state when not dirty", () => {
    const { app } = makeMcpApp();
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: sampleDraft });
    });
    expect(screen.getByLabelText("Subject")).toHaveValue("Hello");
    const updated: Draft = { ...sampleDraft, subject: "From agent" };
    act(() => {
      app.ontoolresult?.({ structuredContent: updated });
    });
    expect(screen.getByLabelText("Subject")).toHaveValue("From agent");
  });

  it("collapses the conversation by default on mobile", async () => {
    mockViewport(true);
    const { app } = makeMcpApp({ structuredContent: sampleThread });
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: threadDraft });
    });
    const toggle = await screen.findByRole("button", { name: /conversation/i });
    // Collapsed: the ▶ affordance shows and message bodies are not rendered.
    expect(toggle.textContent).toContain("▶");
    expect(screen.queryByText("Second message")).not.toBeInTheDocument();
    // User can expand.
    fireEvent.click(toggle);
    expect(
      screen.getByRole("button", { name: /conversation/i }).textContent,
    ).toContain("▼");
    expect(await screen.findByText("Second message")).toBeInTheDocument();
  });

  it("renders existing draft attachments with a remove control", () => {
    const { app } = makeMcpApp();
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({
        structuredContent: {
          ...sampleDraft,
          attachments: [
            {
              attachment_id: "att-1",
              filename: "report.pdf",
              mime_type: "application/pdf",
              size: 2048,
            },
          ],
        },
      });
    });
    expect(screen.getByText("report.pdf")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /remove report\.pdf/i }),
    ).toBeInTheDocument();
  });

  it("uploads a chosen file via save_draft with base64 and keeps existing refs", async () => {
    const savedDraft = {
      structuredContent: {
        draft_id: "d-1",
        to: "bob@example.com",
        subject: "Hello",
        body: "Hi Bob",
        attachments: [
          { attachment_id: "att-existing", filename: "old.pdf", size: 10 },
          { attachment_id: "att-new", filename: "hello.txt", size: 5 },
        ],
      },
    };
    const { app, callServerTool } = makeMcpApp(savedDraft);
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({
        structuredContent: {
          ...sampleDraft,
          attachments: [
            { attachment_id: "att-existing", filename: "old.pdf", size: 10 },
          ],
        },
      });
    });
    const file = new File(["hello"], "hello.txt", { type: "text/plain" });
    const input = screen.getByLabelText("Attach files", {
      selector: "input",
    }) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } });
    });
    await waitFor(() => {
      expect(callServerTool).toHaveBeenCalledWith({
        name: "gmail_composer.save_draft",
        arguments: expect.objectContaining({
          draft_id: "d-1",
          // Existing file preserved by reference; new file uploaded as base64
          // ("hello" -> aGVsbG8=).
          attachments: [
            { attachment_id: "att-existing" },
            {
              filename: "hello.txt",
              mime_type: "text/plain",
              data_base64: "aGVsbG8=",
            },
          ],
        }),
      });
    });
    // The save_draft response echoes both files; the transient chip is replaced
    // by the persisted attachment.
    expect(await screen.findByText("hello.txt")).toBeInTheDocument();
  });

  it("rejects an oversized file without calling save_draft", async () => {
    const { app, callServerTool } = makeMcpApp();
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: sampleDraft });
    });
    const big = new File(["x"], "big.zip", { type: "application/zip" });
    Object.defineProperty(big, "size", { value: 26 * 1024 * 1024 });
    const input = screen.getByLabelText("Attach files", {
      selector: "input",
    }) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, { target: { files: [big] } });
    });
    expect(callServerTool).not.toHaveBeenCalled();
    expect(await screen.findByText(/too large/i)).toBeInTheDocument();
  });

  it("removes an existing attachment via save_draft without its id", async () => {
    const savedDraft = { structuredContent: { ...sampleDraft, attachments: [] } };
    const { app, callServerTool } = makeMcpApp(savedDraft);
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({
        structuredContent: {
          ...sampleDraft,
          attachments: [
            { attachment_id: "att-1", filename: "keep.pdf" },
            { attachment_id: "att-2", filename: "drop.pdf" },
          ],
        },
      });
    });
    fireEvent.click(screen.getByRole("button", { name: /remove drop\.pdf/i }));
    await waitFor(() => {
      expect(callServerTool).toHaveBeenCalledWith({
        name: "gmail_composer.save_draft",
        arguments: expect.objectContaining({
          draft_id: "d-1",
          attachments: [{ attachment_id: "att-1" }],
        }),
      });
    });
  });

  it("expands the conversation by default on desktop", async () => {
    mockViewport(false);
    const { app } = makeMcpApp({ structuredContent: sampleThread });
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: threadDraft });
    });
    const toggle = await screen.findByRole("button", { name: /conversation/i });
    expect(toggle.textContent).toContain("▼");
    // Latest message is expanded by default, so its body is visible.
    expect(await screen.findByText("Second message")).toBeInTheDocument();
  });
});
