/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";
import { sharedServer, sharedTestInclude } from "../_shared/viteShared";

export default defineConfig({
  plugins: [react(), viteSingleFile()],
  server: sharedServer,
  build: {
    outDir: "dist",
    rollupOptions: {
      input: "mcp-app.html",
    },
  },
  test: {
    // jsdom (not happy-dom) - DOMPurify needs a spec-compliant DOM to sanitize
    // email HTML; under happy-dom it collapses sanitized markup to bare text.
    environment: "jsdom",
    include: sharedTestInclude,
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
