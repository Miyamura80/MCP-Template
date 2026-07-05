// Assemble a standalone, fixture-driven preview HTML for an MCP App.
//
//   APP=gmail_inbox bun run build.mjs
//
// Bundles the host bridge (src/host.ts), inlines the committed app bundle
// (mcp_server/apps/<APP>/dist/mcp-app.html) as base64, and writes a single
// self-contained file to dist/<APP>-preview.html. Open it in any browser -
// no server, no Gmail, no OAuth, no network.
import { readFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "..", "..");
const APP = process.env.APP || process.argv[2] || "gmail_inbox";

const appHtmlPath = join(REPO, "mcp_server", "apps", APP, "dist", "mcp-app.html");
if (!existsSync(appHtmlPath)) {
  console.error(
    `✗ No built bundle for app "${APP}" at ${appHtmlPath}\n` +
      `  Build it first:  make build_apps   (or cd into the app and \`bun run build\`)`,
  );
  process.exit(1);
}

// Bundle the host bridge to a single ESM blob.
const built = await Bun.build({
  entrypoints: [join(HERE, "src", "host.ts")],
  format: "esm",
  minify: false,
  target: "browser",
});
if (!built.success) {
  console.error("✗ host bundle failed:");
  for (const m of built.logs) console.error(m);
  process.exit(1);
}
const hostJs = await built.outputs[0].text();
const appB64 = readFileSync(appHtmlPath).toString("base64");

const page = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>MCP-UI preview · ${APP}</title>
<style>
  :root {
    --bg:#f4f5f7; --card:#fff; --ink:#1f2328; --muted:#5b6470;
    --line:#e3e6ea; --accent:#4f46e5; --chip:#eef0f4; --chip-ink:#3b4453;
    color-scheme: light;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#131519; --card:#1b1e24; --ink:#e7e9ec; --muted:#98a1ac;
            --line:#2a2e35; --accent:#8b85f5; --chip:#23272e; --chip-ink:#c3cad3;
            color-scheme: dark; }
  }
  :root[data-theme="light"] { --bg:#f4f5f7; --card:#fff; --ink:#1f2328; --muted:#5b6470;
    --line:#e3e6ea; --accent:#4f46e5; --chip:#eef0f4; --chip-ink:#3b4453; color-scheme: light; }
  :root[data-theme="dark"] { --bg:#131519; --card:#1b1e24; --ink:#e7e9ec; --muted:#98a1ac;
    --line:#2a2e35; --accent:#8b85f5; --chip:#23272e; --chip-ink:#c3cad3; color-scheme: dark; }
  body { margin:0; background:var(--bg); color:var(--ink);
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  .wrap { max-width:1000px; margin:0 auto; padding:28px 20px 40px; }
  .eyebrow { font:600 11px/1 ui-monospace, Menlo, monospace; letter-spacing:.14em;
    text-transform:uppercase; color:var(--accent); margin:0 0 10px; }
  h1 { margin:0 0 8px; font-size:clamp(22px,3vw,30px); font-weight:700;
    text-wrap:balance; letter-spacing:-.01em; }
  .lede { margin:0 0 16px; max-width:64ch; color:var(--muted); font-size:14.5px; line-height:1.55; }
  code { font-family: ui-monospace, Menlo, monospace; background:var(--chip);
    color:var(--chip-ink); padding:1px 6px; border-radius:5px; }
  .chips { display:flex; flex-wrap:wrap; gap:8px; margin:0 0 20px; }
  .chip { display:inline-flex; align-items:center; gap:6px; padding:5px 10px; border-radius:999px;
    background:var(--chip); color:var(--chip-ink); font:600 12px/1 ui-monospace, Menlo, monospace; }
  .chip::before { content:""; width:6px; height:6px; border-radius:50%; background:var(--accent); }
  .frame { background:#fff; border:1px solid var(--line); border-radius:14px; overflow:hidden;
    box-shadow:0 1px 2px rgba(0,0,0,.04), 0 8px 30px rgba(20,22,26,.10); }
  iframe { width:100%; height:670px; border:0; display:block; background:#fff; }
  .foot { margin-top:14px; color:var(--muted); font-size:12.5px; line-height:1.5; }
  #err { color:#c0392b; white-space:pre-wrap; padding:8px 2px; font:12px ui-monospace, monospace; }
</style>
</head>
<body>
<div class="wrap">
  <p class="eyebrow">MCP-UI · dev preview</p>
  <h1>${APP} app, rendered from fixtures</h1>
  <p class="lede">The committed <code>mcp_server/apps/${APP}/dist/mcp-app.html</code> bundle
    running inside a fixture host that plays the ext-apps postMessage protocol and answers
    every <b>callServerTool</b> with canned data - no MCP server, no Gmail, no OAuth.</p>
  <div class="chips">
    <span class="chip">no MCP server</span>
    <span class="chip">no Gmail / OAuth</span>
    <span class="chip">no network</span>
    <span class="chip">fake data</span>
  </div>
  <div class="frame"><iframe id="app" title="${APP} MCP App"></iframe></div>
  <p class="foot">Host bridge: <code>@modelcontextprotocol/ext-apps</code> <code>AppBridge</code>
    (no-client mode) driving an about:blank iframe. Regenerate with
    <code>make preview_app APP=${APP}</code>.</p>
  <div id="err"></div>
</div>
<script>
  window.__APP_NAME__ = ${JSON.stringify(APP)};
  window.__APP_HTML_B64__ = "${appB64}";
</script>
<script type="module">
${hostJs}
</script>
</body>
</html>
`;

const outDir = join(HERE, "dist");
mkdirSync(outDir, { recursive: true });
const outPath = join(outDir, `${APP}-preview.html`);
await Bun.write(outPath, page);
console.log(
  `✓ ${APP} preview → ${outPath} (${(page.length / 1_000_000).toFixed(2)} MB)`,
);
console.log(`  Open it in a browser, or run:  make preview_smoke APP=${APP}`);
