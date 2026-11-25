// The slice of the ext-apps `App` class our apps use, typed structurally so
// component tests can pass a hand-rolled mock and production can pass the
// real `App`. Each app's appContract.test.ts pins the real class against this
// type with `tsc` (`bun run test` type-checks first), so if the SDK renames or
// reshapes a member, every app that installs that SDK version fails.
//
// This folder imports no packages: it has no node_modules of its own, and each
// app resolves its own copy of every dependency.

// Method syntax makes the parameter bivariant. The real `ontoolresult` takes a
// typed CallToolResult, but handlers here receive `unknown` and narrow with
// extractStructuredContent, so a strictly contravariant `(raw: unknown)` would
// reject the real class.
type ToolResultHandler = { bivarianceHack(raw: unknown): void }["bivarianceHack"];

export type McpAppSurface = {
  ontoolresult?: ToolResultHandler;
  callServerTool(args: { name: string; arguments: Record<string, unknown> }): Promise<unknown>;
  openLink(params: { url: string }): Promise<unknown>;
  updateModelContext(args: { content: Array<{ type: "text"; text: string }> }): Promise<unknown>;
  sendMessage(params: { role: "user"; content: Array<{ type: "text"; text: string }> }): Promise<unknown>;
};

export type McpAppMethod = Exclude<keyof McpAppSurface, "ontoolresult">;

/**
 * The members one app needs: `ontoolresult`, the methods it always calls
 * (`Required`), and the ones it calls only when present (`Optional`, so a test
 * mock may leave them out).
 */
export type McpAppLike<Required extends McpAppMethod, Optional extends McpAppMethod = never> = Pick<
  McpAppSurface,
  "ontoolresult" | Required
> &
  Partial<Pick<McpAppSurface, Optional>>;

export type ToolResultBuffer = {
  /**
   * Take over live `ontoolresult` delivery with `handler`, first replaying
   * (oldest first) any results the host delivered before this call.
   */
  drainInto: (handler: (raw: unknown) => void) => void;
};

/**
 * Capture `ontoolresult` deliveries from the earliest possible moment.
 *
 * The host can deliver the initiating tool result before the React component's
 * mount effect assigns its `ontoolresult` handler, and that result would
 * otherwise be lost. Install this in the entry point (before `connect()` and
 * render); the component calls `drainInto(handler)` on mount to replay and take
 * over.
 *
 * Replay-then-assign is gapless: there is no `await` between the two, so no
 * postMessage-driven `ontoolresult` can interleave and be dropped.
 */
export function bufferToolResults(app: Pick<McpAppSurface, "ontoolresult">): ToolResultBuffer {
  const pending: unknown[] = [];
  app.ontoolresult = (raw) => {
    pending.push(raw);
  };
  return {
    drainInto(handler) {
      const buffered = pending.splice(0, pending.length);
      for (const raw of buffered) handler(raw);
      app.ontoolresult = handler;
    },
  };
}
