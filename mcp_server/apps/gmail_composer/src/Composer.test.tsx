import { describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, fireEvent } from "@testing-library/react";
import { Composer, type Draft } from "./Composer";

function makeMcpApp(callResult: unknown = null) {
  const callServerTool = vi.fn(async () => callResult);
  const app = {
    ontoolresult: undefined as ((raw: unknown) => void) | undefined,
    callServerTool,
  };
  return { app, callServerTool };
}

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
});
