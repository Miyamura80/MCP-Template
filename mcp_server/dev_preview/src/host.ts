// Fixture host for MCP-UI apps.
//
// Plays the *host* side of the ext-apps postMessage protocol (the counterpart
// to the `App` client each app bundle runs) using a no-client `AppBridge`, and
// answers every `callServerTool` from local fixtures. This renders a committed
// `dist/mcp-app.html` bundle with zero backing services - no MCP server, no
// Gmail, no OAuth, no network.
//
// Two reads at runtime, injected by build.mjs into the generated HTML:
//   window.__APP_NAME__      e.g. "gmail_inbox" (selects the initial payload)
//   window.__APP_HTML_B64__  the base64 app bundle (optional; falls back to
//                            fetch("./app.html") for the served dev flow)
import {
  AppBridge,
  PostMessageTransport,
} from "@modelcontextprotocol/ext-apps/app-bridge";
import { dispatch, initialResult } from "./fixtures";

const IMPLEMENTATION = { name: "mcp-ui-fixture-host", version: "0.1.0" };

type Globals = {
  __APP_NAME__?: string;
  __APP_HTML_B64__?: string;
  __READY__?: boolean;
};

function appName(): string {
  return (window as unknown as Globals).__APP_NAME__ ?? "gmail_inbox";
}

async function appHtml(): Promise<string> {
  const inlined = (window as unknown as Globals).__APP_HTML_B64__;
  if (inlined) {
    return new TextDecoder().decode(
      Uint8Array.from(atob(inlined), (c) => c.charCodeAt(0)),
    );
  }
  return (await fetch("./app.html")).text();
}

async function main(): Promise<void> {
  const iframe = document.getElementById("app") as HTMLIFrameElement;
  const win = iframe.contentWindow!;

  const bridge = new AppBridge(
    null,
    IMPLEMENTATION,
    { openLinks: {}, updateModelContext: { text: {} } },
    {
      hostContext: {
        theme: "light",
        platform: "web",
        containerDimensions: { width: 920, maxHeight: 6000 },
        displayMode: "inline",
        availableDisplayModes: ["inline", "fullscreen"],
      },
    },
  );

  bridge.oncalltool = async ({ name, arguments: args }) =>
    dispatch(name, (args ?? {}) as Record<string, unknown>);
  bridge.onopenlink = async () => ({});
  bridge.onmessage = async () => ({});
  bridge.onupdatemodelcontext = async () => ({});
  bridge.onrequestdisplaymode = async ({ mode }) => ({ mode });

  bridge.oninitialized = async () => {
    // ext-apps contract: sendToolInput must precede sendToolResult.
    await bridge.sendToolInput({ arguments: {} });
    const result = initialResult(appName());
    // Some apps register their `ontoolresult` handler only after React mounts
    // (i.e. after connect() resolves), so a result sent the instant the app
    // reports initialized can be missed - the inbox self-heals via its own
    // fallback fetch, the composer has none. Re-send over the first ~700ms to
    // cover late registration. Resending the same payload is idempotent, and
    // the window closes before the user could have edited anything.
    await bridge.sendToolResult(result);
    for (const delay of [200, 600]) {
      setTimeout(() => void bridge.sendToolResult(result), delay);
    }
    (window as unknown as Globals).__READY__ = true;
  };

  // Attach the host transport BEFORE the app document runs so the app's
  // `ui/initialize` request is never missed. Writing into the about:blank
  // iframe keeps `contentWindow` stable (no navigation → event.source matches),
  // which is what makes a single-iframe host work without the upstream
  // double-iframe sandbox relay.
  await bridge.connect(new PostMessageTransport(win, win));

  const html = await appHtml();
  const doc = iframe.contentDocument!;
  doc.open();
  doc.write(html);
  doc.close();
}

main().catch((e: unknown) => {
  const el = document.getElementById("err");
  const err = e as { stack?: string } | undefined;
  if (el) el.textContent = "Host error: " + (err?.stack ?? String(e));
});
