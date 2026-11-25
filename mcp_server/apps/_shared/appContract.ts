// Shared body of every app's appContract.test.ts. The call site passes a real
// ext-apps `App` as `McpAppSurface`, so `tsc` fails there if the SDK version
// that app installed no longer matches; these runtime checks catch a member
// that type-checks but is missing at runtime.
import type { McpAppMethod, McpAppSurface } from "./mcpApp";

// A record keyed by every method, so adding one to McpAppSurface fails tsc
// here until the runtime check covers it too.
const METHOD_KEYS = {
  callServerTool: true,
  openLink: true,
  updateModelContext: true,
  sendMessage: true,
} satisfies Record<McpAppMethod, true>;
const METHODS = Object.keys(METHOD_KEYS) as McpAppMethod[];

export function describeAppContract(app: McpAppSurface): void {
  describe("McpAppSurface contract vs real ext-apps App", () => {
    it.each(METHODS)("exposes %s as a method", (name) => {
      expect(typeof app[name]).toBe("function");
    });

    it("ontoolresult is a settable callback property", () => {
      const cb = () => undefined;
      app.ontoolresult = cb;
      expect(app.ontoolresult).toBe(cb);
    });
  });
}
