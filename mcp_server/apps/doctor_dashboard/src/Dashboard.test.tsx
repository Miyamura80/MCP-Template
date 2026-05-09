import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { Dashboard, type DoctorResult } from "./Dashboard";

function makeMcpApp(initial?: DoctorResult) {
  const callServerTool = vi.fn(async () => initial ?? null);
  const app = {
    ontoolresult: undefined as ((raw: unknown) => void) | undefined,
    callServerTool,
  };
  return { app, callServerTool };
}

const sampleResult: DoctorResult = {
  checks: [
    { name: "Python version", status: "pass", message: "3.12.0" },
    { name: "Deps synced", status: "fail", message: "out of sync", fixable: true },
  ],
  has_failures: true,
};

describe("Dashboard", () => {
  it("renders loading state before data arrives", () => {
    const { app } = makeMcpApp();
    render(<Dashboard mcpApp={app} />);
    expect(screen.getByText(/loading checks/i)).toBeInTheDocument();
  });

  it("renders checks once ontoolresult fires", async () => {
    const { app } = makeMcpApp();
    render(<Dashboard mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: sampleResult });
    expect(await screen.findByText("Python version")).toBeInTheDocument();
    expect(screen.getByText("Deps synced")).toBeInTheDocument();
  });

  it("calls doctor tool with fix=true when Auto-Fix is clicked", async () => {
    const { app, callServerTool } = makeMcpApp();
    render(<Dashboard mcpApp={app} />);
    app.ontoolresult?.({ structuredContent: sampleResult });
    const button = await screen.findByRole("button", { name: /auto-fix/i });
    fireEvent.click(button);
    await waitFor(() => {
      expect(callServerTool).toHaveBeenCalledWith({
        name: "doctor",
        arguments: { fix: true },
      });
    });
  });

  it("does not show Auto-Fix when there are no failures", () => {
    const { app } = makeMcpApp();
    render(<Dashboard mcpApp={app} />);
    app.ontoolresult?.({
      structuredContent: { checks: [], has_failures: false } satisfies DoctorResult,
    });
    expect(screen.queryByRole("button", { name: /auto-fix/i })).not.toBeInTheDocument();
  });
});
