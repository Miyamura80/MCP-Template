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
  // Server name used in client configs / deep links (no spaces).
  serverName: "gmail-mcp",
} as const;

export const nav: {
  links: NavLink[];
  github: { href: string; label: string; title: string };
  cta: NavLink;
} = {
  links: [
    { label: "Features", href: "#features" },
    { label: "How it works", href: "#how-it-works" },
    { label: "Docs", href: site.docsUrl },
  ],
  // Highlighted in the header to signal the project is open source & self-hostable.
  github: {
    href: site.githubUrl,
    label: "Open source",
    title: "Open source & self-hostable, view on GitHub",
  },
  cta: { label: "Get started", href: "#how-it-works" },
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
        "No install required: the HTTP API is live at your deployment URL. Authenticate with a bearer token and call it from anything.",
      setupKind: "code",
      setupLang: "bash",
      setupCode: `export GMAILMCP_URL=https://mcp.gmailmcp.com
export TOKEN=sk-...   # OAuth 2.1 bearer`,
      callTitle: "Call a tool",
      callBody:
        "Hit the same service over plain HTTP: identical inputs and outputs as the CLI and MCP.",
      callLang: "bash",
      callCode: `$ curl -s $GMAILMCP_URL/api/v1/services/gmail_curate_inbox \\
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
  // Most dev-tool pages defer pricing to a separate page - flip to false to hide.
  enabled: false,
  heading: "Simple, honest pricing",
  subhead: "Start free. Upgrade when you ship.",
  tiers: [
    {
      name: "Open Source",
      price: "$0",
      description: "Self-host the full template, forever.",
      features: ["All three transports", "OAuth + billing scaffolding", "Community support"],
      cta: "Get started",
      href: "#how-it-works",
    },
    {
      name: "Pro",
      price: "$20",
      cadence: "/mo",
      description: "Hosted, managed, and monitored.",
      features: ["Managed deployment", "Usage analytics", "Priority support"],
      cta: "Start free trial",
      href: "#how-it-works",
      featured: true,
    },
    {
      name: "Team",
      price: "Custom",
      description: "For teams running agents in production.",
      features: ["SSO + audit logs", "SLA", "Dedicated support"],
      cta: "Contact sales",
      href: "#how-it-works",
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
        { label: "Features", href: "#features" },
        { label: "How it works", href: "#how-it-works" },
        // The #pricing section only renders when pricing.enabled - don't link a dead anchor otherwise.
        ...(pricing.enabled ? [{ label: "Pricing", href: "#pricing" }] : []),
      ],
    },
    {
      heading: "Resources",
      links: [
        { label: "Docs", href: site.docsUrl },
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
