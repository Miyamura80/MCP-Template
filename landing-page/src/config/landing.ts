/**
 * Single source of truth for the landing page.
 *
 * This page is data-driven: editing the values below re-skins the entire
 * site. Swapping in a real product should be a config edit, not a rewrite.
 * Optional sections (logoWall, pricing) are gated by `enabled` flags.
 *
 * Search for `TODO` to find every placeholder you must replace.
 */

export interface NavLink {
  label: string;
  href: string;
}

export interface Feature {
  /** Selects the bespoke diagram in FeatureVisual.astro (by key). */
  visual: string;
  title: string;
  body: string;
}

export interface Testimonial {
  quote: string;
  name: string;
  title: string;
  /**
   * Avatar in public/avatars/. The shipped images are AI-generated faces
   * (not real people) so the template implies no real endorsement - swap them
   * for your real customers' headshots. Omit to fall back to a name monogram.
   */
  avatar?: string;
}

export interface FaqItem {
  q: string;
  a: string;
}

export interface PricingTier {
  name: string;
  price: string;
  cadence?: string;
  description: string;
  features: string[];
  cta: string;
  href: string;
  featured?: boolean;
}

export interface FooterColumn {
  heading: string;
  links: NavLink[];
}

export const site = {
  // TODO: product identity
  name: "GmailMCP",
  tagline: "An MCP server starter",
  // Used for <title>, meta description, and OG tags.
  description:
    "GmailMCP is a Model Context Protocol server that gives your AI agent real capabilities: one codebase exposed over CLI, MCP, and HTTP.",
  // TODO: the canonical deployed URL (also set `site` in astro.config.mjs).
  url: "https://gmailmcp.com",
  // TODO: links used across nav, footer, and CTAs.
  docsUrl: "https://docs.gmailmcp.com",
  githubUrl: "https://github.com/Miyamura80/MCP-Template",
  // TODO: the deployed streamable-HTTP MCP endpoint users add to their client.
  // This is the URL you paste / one-click-install into Claude, Cursor, etc.
  mcpUrl: "https://mcp.gmailmcp.com/mcp",
  // TODO: the deployed HTTP API base URL (same backend, vanity host for REST).
  apiUrl: "https://api.gmailmcp.com",
  // Server name used in client configs / deep links (no spaces).
  serverName: "gmail-mcp",
} as const;

/**
 * Pre-connect registry branding (SEP-2127 Server Card + MCP registry server.json).
 *
 * `scripts/gen-discovery.ts` reads this (plus `site`) at build time and writes
 * `public/.well-known/mcp/server-card.json` and `public/server.json`. Those are
 * what MCP registries, client "add server" directories, and AI crawlers read to
 * show your server's name, icon, and description BEFORE anyone connects.
 *
 * Title, description, website, repo URL, icon, and the MCP endpoint are all
 * derived from `site` above so you brand the product in one place. The fields
 * below have no marketing-copy equivalent, so they live here. (The advertised
 * `tools[]` surface is NOT here - it is generated from the Python `@service`
 * registry into `tool-surface.generated.json`; see `scripts/gen-discovery.ts`.)
 */
export const serverCard = {
  // Reverse-DNS registry identity, exactly one slash. Usually io.github.<owner>/<repo>.
  name: "io.github.Miyamura80/MCP-Template",
  // SemVer - keep in step with pyproject.toml / server.json when you release.
  version: "0.1.1",
  // Concise capability summary (<=100 chars for the registry server.json schema).
  description: "Give your AI agent real tools - one service registry over CLI, MCP, and HTTP.",
  // repository.source value the MCP registry expects ("github" | "gitlab" | ...).
  repositorySource: "github",
} as const;

export const nav: {
  links: NavLink[];
  github: { href: string; label: string; title: string };
  cta: NavLink;
} = {
  links: [
    // Absolute anchors (with leading "/") so they also work from sub-pages
    // like /compare and /vs/* - a bare "#features" would only resolve on home.
    { label: "Features", href: "/#features" },
    { label: "How it works", href: "/#how-it-works" },
    { label: "Compare", href: "/compare" },
    { label: "API", href: "/api" },
    { label: "Docs", href: site.docsUrl },
  ],
  // Highlighted in the header to signal the project is open source & self-hostable.
  github: {
    href: site.githubUrl,
    label: "Open source",
    title: "Open source & self-hostable, view on GitHub",
  },
  cta: { label: "Get started", href: "/#how-it-works" },
};

export interface Cta {
  label: string;
  href: string;
  /** Optional logo (in public/logos/) rendered inside the button. */
  logo?: string;
}

/**
 * The primary conversion CTAs, rendered identically by the hero and the final
 * CTA (see CtaButtons.astro). Edit once, both sections update.
 *
 * The project is open source and self-hostable, so the repo is the primary
 * CTA. (Claude/ChatGPT have no one-click install deep link - adding a remote
 * MCP server there is a manual paste-the-URL flow - so a "real" deep-linked
 * "Add to Claude" button isn't possible. Editor clients like Cursor/VS Code
 * do support deep links if you ever want to add those.)
 */
export const ctas: { primary: Cta; secondary: Cta } = {
  primary: { label: "View on GitHub", href: site.githubUrl, logo: "/logos/github.svg" },
  secondary: { label: "Read the docs", href: site.docsUrl },
};

export const hero: {
  eyebrow: string;
  headline: string;
  subhead: string;
} = {
  // Optional eyebrow pill (launch/funding/release). Set to "" to hide.
  eyebrow: "",
  // Keep the headline short (< ~44 chars) and benefit/identity-driven.
  headline: "Give your AI agent real tools.",
  subhead:
    "GmailMCP is a Model Context Protocol server you can ship today. One service registry, exposed identically over CLI, MCP, and HTTP, so any agent that speaks MCP can call it.",
};

/**
 * Hero chat mock - a toggle reskins the chat shell to evoke each client while
 * the embedded MCP-app card stays identical (ChatMock.astro). `accent` is a
 * per-client hint applied only to the shell (avatar, top rule); the rendered
 * MCP app stays brand-cyan so it reads as the same app in every client.
 */
export interface ChatClient {
  id: "claude" | "chatgpt" | "goose" | "vscode";
  name: string;
  logo: string;
  accent: string;
}

export const heroChat: { defaultId: ChatClient["id"]; clients: ChatClient[] } = {
  defaultId: "claude",
  clients: [
    { id: "claude", name: "Claude", logo: "/logos/claude.svg", accent: "#d97757" },
    { id: "chatgpt", name: "ChatGPT", logo: "/logos/chatgpt.svg", accent: "#10a37f" },
    { id: "goose", name: "Goose", logo: "/logos/goose.svg", accent: "#e0a458" },
    { id: "vscode", name: "VS Code", logo: "/logos/vscode.svg", accent: "#3794ff" },
  ],
};

/**
 * "Get started" - the consolidated transports + onboarding section.
 *
 * One service registry, three ways to call it. The visitor picks a transport
 * (CLI / MCP / HTTP API) and that one choice drives BOTH steps:
 *   step 1 - how you connect / install for that transport
 *            (MCP expands into a client sub-picker - see ConnectWidget)
 *   step 2 - calling the *same* tool (gmail_curate_inbox) over that transport,
 *            so the "identical behavior, three transports" payoff is visible
 *            the moment you toggle.
 * Keep the step-2 example identical across transports - that parallel IS the pitch.
 */
export interface TransportOption {
  id: "cli" | "mcp" | "api";
  label: string;
  /** Icon in public/logos/, rendered monochrome next to the label. */
  icon: string;
  // Step 1 - connect / install
  setupTitle: string;
  setupBody: string;
  /** "connect" swaps the code panel for the interactive client picker. */
  setupKind: "code" | "connect";
  setupCode?: string;
  setupLang?: string;
  // Step 2 - call a tool
  callTitle: string;
  callBody: string;
  callCode: string;
  callLang: string;
  /**
   * Optional sub-toggle inside step 02 (e.g. MCP headless vs. interactive).
   * When present, the toggle renders below callBody and each variant supplies
   * its own code panel; callCode/callLang act as the fallback for no-JS.
   */
  callVariants?: CallVariant[];
}

/** A toggle option within a transport's "call a tool" step. */
export interface CallVariant {
  id: string;
  label: string;
  /** One-line description shown above the variant's output. */
  body: string;
  /**
   * "code" renders a syntax-highlighted block (headless → JSON output).
   * "app" renders the live MCP-app card (interactive → the same UI as the hero).
   */
  kind: "code" | "app";
  /** Required when kind === "code". */
  code?: string;
  lang?: string;
}

export const getStarted: {
  heading: string;
  subhead: string;
  defaultId: TransportOption["id"];
  transports: TransportOption[];
} = {
  heading: "One tool. Three transports.",
  subhead:
    "Write a service once and call it identically from the CLI, any MCP client, or plain HTTP. Same inputs, same outputs, zero duplicated logic. Pick yours to get set up.",
  defaultId: "mcp",
  transports: [
    {
      id: "cli",
      label: "CLI",
      icon: "/logos/cli.svg",
      setupTitle: "Install the CLI",
      setupBody:
        "Clone the template and sync dependencies with uv. The mymcp command is ready to run.",
      setupKind: "code",
      setupLang: "bash",
      setupCode: `git clone https://github.com/Miyamura80/MCP-Template
cd MCP-Template && make all`,
      callTitle: "Call a tool",
      callBody: "Invoke any service straight from your shell: typed inputs, structured output.",
      callLang: "bash",
      callCode: `$ mymcp gmail-curate-inbox --limit 3

0.86  Re: Q3 contract redlines        legal@acme.com    ✎ draft
0.61  Design review for v2 dashboard  sarah@team.io
0.42  Your invoice is ready           billing@stripe.com`,
    },
    {
      id: "mcp",
      label: "MCP",
      icon: "/logos/mcp.svg",
      setupTitle: "Add it to your client",
      setupBody:
        "The server runs over streamable HTTP, so onboarding is just its URL. Pick your client: one click where deep links are supported, copy-and-paste everywhere else.",
      setupKind: "connect",
      callTitle: "Call a tool",
      callBody:
        "Your agent discovers the tools automatically and calls them with typed inputs. The same service answers two ways:",
      callLang: "jsonc",
      callCode: `// client → server  ·  JSON-RPC over streamable HTTP
{
  "method": "tools/call",
  "params": {
    "name": "gmail_curate_inbox",
    "arguments": { "limit": 3 }
  }
}`,
      callVariants: [
        {
          id: "headless",
          label: "Headless",
          kind: "code",
          body:
            "The default: a pure service returns its typed output model. FastMCP derives the outputSchema, so the CLI, API, and MCP all behave identically.",
          lang: "jsonc",
          code: `// client → server  ·  JSON-RPC over streamable HTTP
{
  "method": "tools/call",
  "params": {
    "name": "gmail_curate_inbox",
    "arguments": { "limit": 3 }
  }
}

// server → client  ·  structured output
{
  "content": [{ "type": "text", "text": "3 threads ranked. Top: Q3 contract redlines" }],
  "structuredContent": {
    "threads": [
      { "subject": "Re: Q3 contract redlines", "importance_score": 0.86, "has_draft": true },
      { "subject": "Design review for v2 dashboard", "importance_score": 0.61, "has_draft": false }
    ]
  }
}`,
        },
        {
          id: "interactive",
          label: "Interactive",
          kind: "app",
          body:
            "Opt the same tool into an @enhance handler and it can elicit input, attach media, or render an MCP App: a sandboxed iframe dashboard your client embeds inline. MCP-only; the CLI and API stay untouched.",
        },
      ],
    },
    {
      id: "api",
      label: "HTTP API",
      icon: "/logos/api.svg",
      setupTitle: "Point at the endpoint",
      setupBody:
        "No install required: the HTTP API is live at its own host. Authenticate with a bearer token and call it from anything.",
      setupKind: "code",
      setupLang: "bash",
      setupCode: `export GMAILMCP_API_URL=${site.apiUrl}
export TOKEN=sk-...   # OAuth 2.1 bearer`,
      callTitle: "Call a tool",
      callBody:
        "Hit the same service over plain HTTP: identical inputs and outputs as the CLI and MCP.",
      callLang: "bash",
      callCode: `$ curl -s $GMAILMCP_API_URL/api/v1/services/gmail_curate_inbox \\
    -H "Authorization: Bearer $TOKEN" \\
    -H "Content-Type: application/json" \\
    -d '{ "limit": 3 }'

{ "threads": [
    { "subject": "Re: Q3 contract redlines", "importance_score": 0.86, "has_draft": true },
    { "subject": "Design review for v2 dashboard", "importance_score": 0.61, "has_draft": false }
] }`,
    },
  ],
};

/**
 * Compatibility / trust strip. Doubles as a capability signal for MCP.
 * Logos live in `public/logos/` and are rendered flattened to a single brand
 * color via CSS mask (see TrustStrip.astro) so full-color marks don't clash
 * with the monochrome Hackbox aesthetic. `logo: null` renders a text monogram
 * fallback - drop an SVG in `public/logos/` and point `logo` at it to upgrade.
 */
export interface Client {
  name: string;
  logo: string | null;
}

export const compatibility: { heading: string; clients: Client[] } = {
  heading: "Works with every MCP client",
  clients: [
    { name: "Claude", logo: "/logos/claude.svg" },
    { name: "Codex", logo: "/logos/codex.svg" },
    { name: "Cursor", logo: "/logos/cursor.svg" },
    { name: "ChatGPT", logo: "/logos/chatgpt.svg" },
    { name: "VS Code", logo: "/logos/vscode.svg" },
    { name: "OpenClaw", logo: "/logos/openclaw.svg" },
    { name: "Goose", logo: "/logos/goose.svg" },
  ],
};

/**
 * Client picker for the "Add it to your client" step (see ConnectWidget.astro).
 *
 * method "deeplink" → a real one-click install URL is built at build time from
 *   site.mcpUrl + site.serverName (Cursor/VS Code/Goose support this).
 * method "manual" → no deep link exists (Claude, ChatGPT), so we show the
 *   server URL to copy plus the click-path to paste it. `steps` are those.
 *
 * Deep-link formats verified against official docs (cursor.com, code.visualstudio.com,
 * goose docs). Claude/ChatGPT have no install URL scheme - paste-the-URL is the
 * only supported flow.
 */
export interface InstallTarget {
  id: "claude" | "chatgpt" | "cursor" | "vscode" | "goose";
  name: string;
  logo: string;
  method: "deeplink" | "manual";
  /** For manual targets: the click-path to paste the URL. */
  steps?: string[];
  /** Optional note rendered under a deep-link button. */
  note?: string;
}

export const connect: {
  mcpUrl: string;
  serverName: string;
  /** id of the target selected by default in the dropdown. */
  defaultId: InstallTarget["id"];
  targets: InstallTarget[];
} = {
  mcpUrl: site.mcpUrl,
  serverName: site.serverName,
  defaultId: "cursor",
  targets: [
    {
      id: "cursor",
      name: "Cursor",
      logo: "/logos/cursor.svg",
      method: "deeplink",
      note: "Opens Cursor and adds the server. Not working? Copy the URL above and add it under Settings → MCP.",
    },
    {
      id: "vscode",
      name: "VS Code",
      logo: "/logos/vscode.svg",
      method: "deeplink",
      note: "Opens VS Code and adds the server. Requires the GitHub Copilot / MCP support.",
    },
    {
      id: "goose",
      name: "Goose",
      logo: "/logos/goose.svg",
      method: "deeplink",
      note: "Opens Goose and adds the extension over streamable HTTP.",
    },
    {
      id: "claude",
      name: "Claude",
      logo: "/logos/claude.svg",
      method: "manual",
      steps: [
        "Open Claude → Settings → Connectors",
        "Click “Add custom connector”",
        "Paste the URL above, then click Add",
      ],
    },
    {
      id: "chatgpt",
      name: "ChatGPT",
      logo: "/logos/chatgpt.svg",
      method: "manual",
      steps: [
        "Settings → Connectors → Advanced: turn on Developer mode",
        "Click Create",
        "Paste the URL above, then click Create",
      ],
    },
  ],
};

export const features: { heading: string; subhead: string; items: Feature[] } = {
  heading: "One codebase, every surface",
  subhead: "Write a tool once. Ship it to agents, scripts, and services without rewrites.",
  items: [
    {
      visual: "transports",
      title: "Three transports, zero duplication",
      body: "Every tool is a pure function in a shared registry, exposed identically over CLI, MCP, and HTTP. Behavior never drifts between interfaces.",
    },
    {
      visual: "interactive",
      title: "Headless or interactive",
      body: "Return data for autonomous agents, or opt into enhanced tools that elicit input, attach media, and render sandboxed UI dashboards.",
    },
  ],
};

/**
 * Competitive comparison - powers the on-page comparison section
 * (Comparison.astro), the /compare hub, the per-competitor /vs/<slug> pages,
 * and the machine-readable "How it compares" block in llms-full.txt / agents.md.
 *
 * Editing this one object updates every comparison surface at once.
 *
 * Honesty rules (keep us credible + AEO-citable):
 * - `pickThem` must give a genuine, fair reason to choose the competitor.
 * - Matrix claims should be defensible as of `asOf`; competitor capabilities
 *   shift fast, so date-stamp and keep cells verifiable, not slanted.
 */
export type MatrixState = "yes" | "no" | "partial";

export interface MatrixCell {
  state: MatrixState;
  /** Optional short qualifier shown under the mark. */
  note?: string;
}

export interface MatrixRow {
  /** Capability being compared. */
  capability: string;
  /** One-line explanation of why this capability matters. */
  detail?: string;
  /** Our value. */
  us: MatrixCell;
  /** Per-competitor value, keyed by competitor `id`. */
  cells: Record<string, MatrixCell>;
}

export interface Competitor {
  /** URL slug → /vs/<id>. */
  id: string;
  name: string;
  /** Bucket label, e.g. "Open-source wrapper". */
  category: string;
  /** One line: what they are. */
  blurb: string;
  /** Canonical link to the competitor. */
  url: string;
  /** Headline contrast for the /vs page hero. */
  headline: string;
  /** Paragraph summary of the difference. */
  summary: string;
  /** Honest "when to pick them" - keeps the page credible + citable. */
  pickThem: string;
  /** "when to pick us". */
  pickUs: string;
}

export interface ComparisonPillar {
  title: string;
  body: string;
}

export const comparison: {
  /** Section + page headings. */
  heading: string;
  subhead: string;
  /** Defensibility stamp surfaced on the comparison pages. */
  asOf: string;
  disclaimer: string;
  /** The three headline differentiators. */
  pillars: ComparisonPillar[];
  competitors: Competitor[];
  matrix: MatrixRow[];
} = {
  heading: "How GmailMCP compares",
  subhead:
    "Most Gmail MCPs hand your agent raw API calls and a wall of JSON. GmailMCP is a Gmail product: an interactive inbox you can drive from inside the chat, open source and yours to host.",
  asOf: "June 2026",
  disclaimer:
    "Comparison reflects publicly documented capabilities as of June 2026. The MCP ecosystem moves fast - if something here is out of date, open an issue and we'll fix it.",
  pillars: [
    {
      title: "Interactive UI, not just JSON",
      body: "GmailMCP renders MCP Apps - sandboxed UI that lives inside the chat. Review and edit a draft in a real composer, then triage a ranked inbox in an embedded dashboard, all without leaving your agent. The inbox ranking and triage flow exist because the interactive surface makes them useful; other Gmail MCPs return raw search results and stop there.",
    },
    {
      title: "One codebase, three transports",
      body: "Every tool is a pure function in a shared registry, exposed identically over a CLI, an MCP server, and a plain HTTP API. Build once and call it from your shell, any MCP client, or a script - behavior never drifts between interfaces. Most Gmail MCPs are single-transport: stdio-only, or a hosted endpoint you can't run locally.",
    },
    {
      title: "Open source and self-hostable",
      body: "The whole server is open source and ships with a Dockerfile and deploy config, so you can run it on your own infrastructure with your own OAuth credentials and encrypted token storage. Aggregator gateways route your mail through a proprietary service you don't control.",
    },
  ],
  competitors: [
    {
      id: "gongrzhe-gmail-mcp",
      name: "GongRzhe Gmail-MCP-Server",
      category: "Open-source wrapper",
      blurb:
        "The most-starred open-source Gmail MCP: a local stdio server wrapping the Gmail API.",
      url: "https://github.com/GongRzhe/Gmail-MCP-Server",
      headline: "The open-source Gmail MCP, upgraded.",
      summary:
        "GongRzhe's server is a faithful, well-loved wrapper around the Gmail API - around a dozen tools for send, draft, read, search, labels and attachments, run locally over stdio with a credentials file on disk. You get clean primitives and JSON back. GmailMCP shares the open-source spirit but goes further: it renders an interactive composer and a ranked-inbox dashboard inside the chat, and the same tools are reachable over a CLI and an HTTP API, not just stdio.",
      pickThem:
        "you want a minimal, local, stdio-only Gmail wrapper to embed in a desktop client and you're happy driving everything through JSON tool calls.",
      pickUs:
        "you want an in-chat composer and inbox triage UI, remote zero-install access, and the same tools available over CLI and HTTP as well as MCP.",
    },
    {
      id: "composio-gmail",
      name: "Composio Gmail",
      category: "Aggregator gateway",
      blurb:
        "Gmail as one toolkit inside a managed 500+ app MCP gateway with hosted OAuth.",
      url: "https://composio.dev/toolkits/gmail",
      headline: "A Gmail product, not a Gmail endpoint in a 500-app gateway.",
      summary:
        "Composio's strength is breadth: one managed endpoint and hosted OAuth across hundreds of SaaS apps, with Gmail exposed as a generic search / read / draft / send toolkit. GmailMCP trades breadth for depth on email - an interactive composer and ranked-inbox dashboard rendered in the chat - and it's open source, so you self-host with your own credentials instead of routing mail through a proprietary gateway.",
      pickThem:
        "you need one managed endpoint spanning many SaaS apps and you don't want to run any infrastructure yourself.",
      pickUs:
        "email is the job: you want an interactive in-chat inbox, full control of your own deployment, and the ability to run the same tools over CLI and HTTP.",
    },
    {
      id: "zapier-pipedream-mcp",
      name: "Zapier & Pipedream MCP",
      category: "Workflow automation",
      blurb:
        "Gmail actions inside no-code automation platforms exposed as MCP tools.",
      url: "https://zapier.com/mcp",
      headline: "Built for an agent in the loop, not a no-code workflow.",
      summary:
        "Zapier and Pipedream expose Gmail as actions inside their automation platforms - great for fire-and-forget workflows, with tools often auto-generated from API specs. GmailMCP is purpose-built for a human-in-the-loop agent: an interactive composer where you review and edit before anything sends, a ranked inbox you triage in-chat, and an open-source codebase you host yourself rather than orchestrate through a workflow runner.",
      pickThem:
        "your goal is automated, multi-app workflows triggered by events, and you're already invested in their builder.",
      pickUs:
        "you want an agent that drafts and triages with you interactively, a focused Gmail surface, and a server you own and self-host.",
    },
    {
      id: "google-workspace-mcp",
      name: "Google Workspace MCP",
      category: "Workspace suite",
      blurb:
        "Broad Google Workspace coverage (Gmail, Calendar, Drive, Docs) over MCP.",
      url: "https://github.com/taylorwilsdon/google_workspace_mcp",
      headline: "Gmail done deeply vs. Workspace done broadly.",
      summary:
        "Google Workspace MCP covers a huge surface - Gmail, Calendar, Drive and Docs - as headless tools you run yourself. It's a great fit when you need the whole suite. GmailMCP goes the other way: deep on Gmail with an interactive in-chat composer and ranked-inbox dashboard, a minimal single Gmail scope, and the same tools exposed over CLI and HTTP as well as MCP.",
      pickThem:
        "you need Calendar, Drive and Docs alongside Gmail in one server.",
      pickUs:
        "Gmail is the priority and you want an interactive inbox UI, scope minimalism, and multi-transport access.",
    },
  ],
  matrix: [
    {
      capability: "Interactive in-chat UI (MCP Apps)",
      detail: "Composer + ranked-inbox dashboard rendered inside the chat client.",
      us: { state: "yes" },
      cells: {
        "gongrzhe-gmail-mcp": { state: "no", note: "JSON only" },
        "composio-gmail": { state: "no", note: "JSON only" },
        "zapier-pipedream-mcp": { state: "no", note: "JSON only" },
        "google-workspace-mcp": { state: "no", note: "JSON only" },
      },
    },
    {
      capability: "Built-in inbox triage & ranking",
      detail: "An importance-ranked inbox out of the box, not just raw search.",
      us: { state: "yes" },
      cells: {
        "gongrzhe-gmail-mcp": { state: "no", note: "raw search" },
        "composio-gmail": { state: "no", note: "raw search" },
        "zapier-pipedream-mcp": { state: "partial", note: "build a workflow" },
        "google-workspace-mcp": { state: "no", note: "raw search" },
      },
    },
    {
      capability: "One codebase → CLI + MCP + HTTP",
      detail: "Identical tools across every interface, no duplicated logic.",
      us: { state: "yes" },
      cells: {
        "gongrzhe-gmail-mcp": { state: "partial", note: "stdio / HTTP" },
        "composio-gmail": { state: "no", note: "hosted endpoint" },
        "zapier-pipedream-mcp": { state: "no", note: "hosted endpoint" },
        "google-workspace-mcp": { state: "partial", note: "MCP only" },
      },
    },
    {
      capability: "Zero-install (remote streamable HTTP)",
      detail: "Connect by pasting a URL - nothing to install locally.",
      us: { state: "yes" },
      cells: {
        "gongrzhe-gmail-mcp": { state: "no", note: "local install" },
        "composio-gmail": { state: "yes" },
        "zapier-pipedream-mcp": { state: "yes" },
        "google-workspace-mcp": { state: "no", note: "self-run" },
      },
    },
    {
      capability: "Open source & self-hostable",
      detail: "Run the full server on your own infrastructure.",
      us: { state: "yes" },
      cells: {
        "gongrzhe-gmail-mcp": { state: "yes" },
        "composio-gmail": { state: "no", note: "proprietary" },
        "zapier-pipedream-mcp": { state: "no", note: "proprietary" },
        "google-workspace-mcp": { state: "yes" },
      },
    },
    {
      capability: "Minimal scope + encrypted tokens",
      detail: "A single Gmail scope with tokens encrypted at rest.",
      us: { state: "yes" },
      cells: {
        "gongrzhe-gmail-mcp": { state: "partial", note: "local token file" },
        "composio-gmail": { state: "yes", note: "managed" },
        "zapier-pipedream-mcp": { state: "yes", note: "managed" },
        "google-workspace-mcp": { state: "partial", note: "broad scopes" },
      },
    },
  ],
};

export const testimonials: { enabled: boolean; heading: string; items: Testimonial[] } = {
  enabled: true,
  heading: "Trusted by builders",
  items: [
    {
      // TODO: replace with real quotes - even one line from a first user beats nothing.
      quote: "We had a production MCP server in front of our agents the same afternoon. The shared registry meant our CLI and API just worked too.",
      name: "Placeholder Name",
      title: "Staff Engineer, Placeholder Co.",
      avatar: "/avatars/person-1.jpg",
    },
    {
      quote: "The typed schemas are the killer feature. Our agent stopped guessing argument shapes overnight.",
      name: "Placeholder Name",
      title: "Founder, Placeholder AI",
      avatar: "/avatars/person-2.jpg",
    },
  ],
};

export const pricing: { enabled: boolean; heading: string; subhead: string; tiers: PricingTier[] } = {
  // Surfaced on the homepage AND in the machine-readable /pricing.md manifest.
  // Flip to false to hide the on-page section (the manifest still generates).
  enabled: true,
  heading: "Pricing & licensing",
  subhead:
    "Open source under the MIT license and free to self-host - no setup fee, no seat minimum. Pay only when you want us to run and scale it for you.",
  tiers: [
    {
      name: "Open Source",
      price: "$0",
      cadence: "forever",
      description:
        "MIT-licensed. Self-host the full server on your own infrastructure - zero setup cost, no license fee.",
      features: [
        "MIT license - fork, modify, and ship freely",
        "All three transports: CLI, MCP, HTTP API",
        "Interactive MCP Apps (composer + ranked inbox)",
        "Your own OAuth credentials & encrypted tokens",
        "Community support",
      ],
      cta: "Get the source",
      href: site.githubUrl,
    },
    {
      name: "Hosted Pro",
      price: "$20",
      cadence: "/mo",
      description:
        "We run the streamable-HTTP server for you. No infrastructure to manage, paste-a-URL setup.",
      features: [
        "Managed cloud deployment (zero ops)",
        "Hosted OAuth 2.1 & encrypted token storage",
        "Usage analytics & monitoring",
        "Priority support",
      ],
      cta: "Start free trial",
      href: "/#how-it-works",
      featured: true,
    },
    {
      name: "Team",
      price: "Custom",
      description: "For teams running agents in production, with commercial licensing options.",
      features: [
        "SSO + audit logs",
        "Commercial / OEM licensing",
        "Uptime SLA",
        "Dedicated support & onboarding",
      ],
      cta: "Contact sales",
      href: "/#how-it-works",
    },
  ],
};

/**
 * "Ask AI about this" - links that open an assistant with a pre-filled prompt
 * about the project. Each provider URL has a `{q}` placeholder; AskAi.astro
 * substitutes the encoded prompt at build time.
 */
export interface AskAiProvider {
  id: "chatgpt" | "perplexity" | "claude";
  name: string;
  logo: string;
  url: string;
}

export const askAi: {
  heading: string;
  subhead: string;
  prompt: string;
  providers: AskAiProvider[];
} = {
  heading: "Ask AI about this",
  subhead: "Have your assistant explain the template, compare it, or walk you through deploying it.",
  prompt: `What is the ${site.name} MCP server template? Explain what it does, how the CLI / MCP / HTTP transports share one codebase, and how I'd deploy it. Repo: ${site.githubUrl}`,
  providers: [
    { id: "chatgpt", name: "ChatGPT", logo: "/logos/chatgpt.svg", url: "https://chatgpt.com/?q={q}" },
    { id: "perplexity", name: "Perplexity", logo: "/logos/perplexity.svg", url: "https://www.perplexity.ai/search?q={q}" },
    { id: "claude", name: "Claude", logo: "/logos/claude.svg", url: "https://claude.ai/new?q={q}" },
  ],
};

export const faq: { heading: string; items: FaqItem[] } = {
  heading: "Frequently asked questions",
  items: [
    {
      q: "Which MCP clients are supported?",
      a: "Any client that speaks the Model Context Protocol: Claude Desktop, Claude Code, Cursor, Cline, VS Code, Windsurf, and more. The server exposes a standard tool/resource surface.",
    },
    {
      q: "stdio or streamable HTTP?",
      a: "Both. Streamable HTTP is the primary transport (mounted at /mcp alongside the HTTP API in one process), and stdio is available for local/dev use.",
    },
    {
      q: "How does authentication work?",
      a: "The MCP mount supports OAuth 2.1 as a resource server, sharing auth and CORS with the HTTP API. You can also run it unauthenticated for local development.",
    },
    {
      q: "Do I need to install anything to use it?",
      a: "No. Because the server runs over streamable HTTP, connecting is just pasting its URL into your agent client. No local install, runtime, or download required. (Self-hosting the server is a separate, optional step.)",
    },
    {
      q: "Does it work on mobile?",
      a: "Yes, anywhere your agent runs. Since it's a remote HTTP server with nothing to install locally, it works in any agent app that has a mobile app, including the Claude and ChatGPT mobile apps.",
    },
    {
      q: "Can I self-host?",
      a: "Yes. The whole thing is open source and ships with a Dockerfile and Railway config. Deploy it anywhere that runs a container.",
    },
    {
      q: "What about my existing CLI / API?",
      a: "They share the same service registry. Add a tool once and it's available over CLI, MCP, and HTTP simultaneously, with no duplicated logic.",
    },
    {
      q: "Is this just another Gmail API wrapper?",
      a: "No. Most Gmail MCPs (like GongRzhe's) wrap the Gmail API and return JSON. GmailMCP renders interactive MCP Apps - a composer you edit drafts in and a ranked inbox you triage - directly inside the chat, and exposes the same tools over CLI and HTTP, not just MCP. See the comparison at /compare.",
    },
    {
      q: "How is it different from Composio, Zapier, or Google Workspace MCP?",
      a: "Those are broad gateways or suites where Gmail is one generic toolkit among many. GmailMCP goes deep on Gmail with an interactive in-chat inbox, and it's open source and self-hostable so your mail never routes through a proprietary service. Full breakdown at /compare.",
    },
  ],
};

export const finalCta: { heading: string; subhead: string; features: string[] } = {
  heading: "Ship your MCP server today.",
  subhead: "Clone the template, deploy it, and point your agent at the URL.",
  // Four flagship features, 3–4 words each, shown beside the final CTA.
  features: [
    "Three transports, one codebase",
    "Headless or interactive tools",
    "Streamable HTTP, one port",
    "Open source, self-hostable",
  ],
};

export const footer: { columns: FooterColumn[]; copyright: string } = {
  columns: [
    {
      heading: "Product",
      links: [
        { label: "Features", href: "/#features" },
        { label: "How it works", href: "/#how-it-works" },
        { label: "Compare", href: "/compare" },
        // The #pricing section only renders when pricing.enabled - don't link a dead anchor otherwise.
        // Absolute (/#pricing) so it also resolves from sub-pages like /compare and /vs/*.
        ...(pricing.enabled ? [{ label: "Pricing", href: "/#pricing" }] : []),
      ],
    },
    {
      heading: "Resources",
      links: [
        { label: "Docs", href: site.docsUrl },
        { label: "API Reference", href: "/api" },
        { label: "GitHub", href: site.githubUrl },
        { label: "Changelog", href: site.githubUrl + "/releases" },
      ],
    },
    {
      heading: "Company",
      links: [
        { label: "About", href: "#" },
        { label: "Blog", href: "#" },
        { label: "Contact", href: "#" },
      ],
    },
    {
      heading: "Legal",
      links: [
        { label: "Privacy", href: "/privacy" },
        { label: "Terms", href: "/terms" },
        { label: "Security", href: "#" },
      ],
    },
  ],
  copyright: `© ${new Date().getFullYear()} ${site.name}. All rights reserved.`,
};
