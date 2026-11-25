// jsdom lacks the canvas APIs that pdfjs-dist touches at import time. App tests
// never rasterize a real PDF, so empty classes are enough to let it load.
type Ctor = new (...args: unknown[]) => object;
const NAMES = ["DOMMatrix", "Path2D", "ImageData"] as const;
const g = globalThis as unknown as Record<(typeof NAMES)[number], Ctor | undefined>;
for (const name of NAMES) {
  if (typeof g[name] === "undefined") {
    g[name] = class {};
  }
}
