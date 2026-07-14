import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, fireEvent } from "@testing-library/react";
import { Composer, type Draft, type Thread } from "./Composer";

function makeMcpApp(callResult: unknown = null) {
  const callServerTool = vi.fn(async () => callResult);
  const updateModelContext = vi.fn(
    async (_args: { content: Array<{ type: "text"; text: string }> }) => ({}),
  );
  const app = {
    ontoolresult: undefined as ((raw: unknown) => void) | undefined,
    callServerTool,
    updateModelContext,
  };
  return { app, callServerTool, updateModelContext };
}

// A save_draft mock that echoes the attachment set it was given back as
// persisted rows (uploads -> a minted server id, refs -> preserved), so the
// component's attachmentsRef tracks committed server state across serialized
// ops - the exact thing the concurrency tests need to exercise.
function makeEchoingMcpApp() {
  const calls: { name: string; args: Record<string, any> }[] = []; // eslint-disable-line @typescript-eslint/no-explicit-any
  const callServerTool = vi.fn(
    async ({ name, arguments: args }: { name: string; arguments: Record<string, any> }) => {   // eslint-disable-line @typescript-eslint/no-explicit-any
      calls.push({ name, args });
      if (name === "gmail_composer.save_draft") {
        const atts = ((args.attachments as any[]) ?? []).map((a) =>   // eslint-disable-line @typescript-eslint/no-explicit-any
          a.data_base64 !== undefined
            ? { attachment_id: `srv-${a.filename}`, filename: a.filename, mime_type: a.mime_type, size: 1 }
            : { attachment_id: a.attachment_id, filename: String(a.attachment_id).replace(/^srv-/, ""), size: 1 },
        );
        return {
          structuredContent: {
            draft_id: args.draft_id,
            to: args.to,
            subject: args.subject,
            body: args.body,
            attachments: atts,
          },
        };
      }
      return { structuredContent: {} };
    },
  );
  const updateModelContext = vi.fn(
    async (_args: { content: Array<{ type: "text"; text: string }> }) => ({}),
  );
  const app = {
    ontoolresult: undefined as ((raw: unknown) => void) | undefined,
    callServerTool,
    updateModelContext,
  };
  return { app, callServerTool, calls };
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

  it("Send pushes the final sent draft into model context", async () => {
    const sendResult = { structuredContent: { message_id: "msg-99" } };
    const { app, updateModelContext } = makeMcpApp(sendResult);
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: sampleDraft });
    });
    // Edit the body first: the context push must carry the user's final
    // version, not the last agent-known draft.
    fireEvent.change(screen.getByLabelText("Body"), {
      target: { value: "Hi Bob - edited by hand" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));
    await waitFor(() => {
      expect(updateModelContext).toHaveBeenCalledTimes(1);
    });
    const text = updateModelContext.mock.calls[0][0].content[0].text;
    expect(text).toContain("clicked Send");
    expect(text).toContain("to: bob@example.com");
    expect(text).toContain("subject: Hello");
    expect(text).toContain("message_id: msg-99");
    expect(text).toContain("Hi Bob - edited by hand");
  });

  it("Send still succeeds when the host rejects the context update", async () => {
    const sendResult = { structuredContent: { message_id: "msg-99" } };
    const { app } = makeMcpApp(sendResult);
    app.updateModelContext = vi
      .fn()
      .mockRejectedValue(new Error("unsupported"));
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: sampleDraft });
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));
    expect(await screen.findByText(/msg-99/)).toBeInTheDocument();
    expect(screen.queryByText(/save failed/i)).not.toBeInTheDocument();
  });

  it("Discard with confirm calls gmail_composer.discard", async () => {
    const { app, callServerTool, updateModelContext } = makeMcpApp({
      structuredContent: { discarded: true },
    });
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
    // The agent must learn the draft is gone, or it will keep referencing it.
    await waitFor(() => {
      expect(updateModelContext).toHaveBeenCalledTimes(1);
    });
    const text = updateModelContext.mock.calls[0][0].content[0].text;
    expect(text).toContain("Discard");
    expect(text).toContain("d-1");
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
    const { app, calls } = makeEchoingMcpApp();
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({
        structuredContent: {
          ...sampleDraft,
          attachments: [{ attachment_id: "att-existing", filename: "old.pdf", size: 10 }],
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
      const save = calls.find((c) => c.name === "gmail_composer.save_draft");
      // Existing file preserved by reference; new file uploaded as base64
      // ("hello" -> aGVsbG8=).
      expect(save?.args.attachments).toEqual([
        { attachment_id: "att-existing" },
        { filename: "hello.txt", mime_type: "text/plain", data_base64: "aGVsbG8=" },
      ]);
    });
    expect(await screen.findByText("hello.txt")).toBeInTheDocument();
  });

  it("rejects an oversized file without calling save_draft", async () => {
    const { app, callServerTool } = makeMcpApp();
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: sampleDraft });
    });
    const big = new File(["x"], "big.zip", { type: "application/zip" });
    Object.defineProperty(big, "size", { value: 26 * 1000 * 1000 });
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
    const { app, calls } = makeEchoingMcpApp();
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
      const save = calls.find((c) => c.name === "gmail_composer.save_draft");
      expect(save?.args.attachments).toEqual([{ attachment_id: "att-1" }]);
    });
  });

  it("an attachment save uses the latest body, not a pre-read snapshot", async () => {
    // Regression: the attachment path must send the freshest text, not the text
    // snapshotted before the async file read (which would revert a live edit).
    const { app, calls } = makeEchoingMcpApp();
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: sampleDraft }); // body "Hi Bob"
    });
    fireEvent.change(screen.getByLabelText("Body"), {
      target: { value: "Hi Bob EDITED" },
    });
    const input = screen.getByLabelText("Attach files", {
      selector: "input",
    }) as HTMLInputElement;
    const file = new File(["x"], "note.txt", { type: "text/plain" });
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } });
    });
    await waitFor(() => {
      const save = calls.find((c) => c.name === "gmail_composer.save_draft");
      expect(save?.args.body).toBe("Hi Bob EDITED");
    });
  });

  it("serializes concurrent uploads so neither file is dropped", async () => {
    // Regression: two quick drops each built their whole-set replace from the
    // same stale draft, so the second clobbered the first's file.
    const { app, calls } = makeEchoingMcpApp();
    render(<Composer mcpApp={app} />);
    act(() => {
      app.ontoolresult?.({ structuredContent: { ...sampleDraft, attachments: [] } });
    });
    const input = screen.getByLabelText("Attach files", {
      selector: "input",
    }) as HTMLInputElement;
    const fileA = new File(["AAA"], "a.txt", { type: "text/plain" });
    const fileB = new File(["BBB"], "b.txt", { type: "text/plain" });
    await act(async () => {
      fireEvent.change(input, { target: { files: [fileA] } });
      fireEvent.change(input, { target: { files: [fileB] } });
    });
    await waitFor(() => {
      const saves = calls.filter((c) => c.name === "gmail_composer.save_draft");
      expect(saves.length).toBe(2);
      // The second save carries BOTH files (a ref to the first + the second),
      // proving it built on the committed result of the first, not a stale draft.
      expect(saves[saves.length - 1].args.attachments).toHaveLength(2);
    });
    await waitFor(() => {
      expect(screen.getByText("a.txt")).toBeInTheDocument();
      expect(screen.getByText("b.txt")).toBeInTheDocument();
    });
  });
});
