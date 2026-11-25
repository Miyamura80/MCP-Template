import { errMsg, extractStructuredContent } from "./toolResult";

describe("extractStructuredContent", () => {
  it("prefers structuredContent", () => {
    const raw = { structuredContent: { a: 1 }, content: [{ type: "text", text: '{"a":2}' }] };
    expect(extractStructuredContent(raw)).toEqual({ a: 1 });
  });

  it("falls back to a JSON TextContent block", () => {
    expect(extractStructuredContent({ content: [{ type: "text", text: '{"a":2}' }] })).toEqual({ a: 2 });
  });

  it("ignores a non-text block that carries a text field", () => {
    const raw = { content: [{ type: "resource", text: '{"a":3}' }] };
    expect(extractStructuredContent(raw)).toBeNull();
  });

  it("skips non-JSON text and keeps looking", () => {
    const raw = { content: [{ type: "text", text: "hello" }, { type: "text", text: '{"a":4}' }] };
    expect(extractStructuredContent(raw)).toEqual({ a: 4 });
  });

  it("never returns the raw envelope", () => {
    expect(extractStructuredContent({ isError: false })).toBeNull();
    expect(extractStructuredContent(null)).toBeNull();
    expect(extractStructuredContent("x")).toBeNull();
  });
});

describe("errMsg", () => {
  it("uses Error.message, else String()", () => {
    expect(errMsg(new Error("boom"))).toBe("boom");
    expect(errMsg(42)).toBe("42");
  });
});
