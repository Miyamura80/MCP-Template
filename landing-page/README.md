# Landing page

A standalone, statically-built marketing landing page for the MCP server product. Built with [Astro](https://astro.build) + Tailwind v4, deployed independently on Railway.

It is **separate** from the `docs/` site (Next.js + Fumadocs) and from the Python server - its own folder, its own deploy.

## TLDR - rebrand it

The entire page is data-driven. Edit **one file** and you've reskinned the site:

```
src/config/landing.ts
```

Search that file for `TODO` to find every placeholder (product name, tagline, install command, GitHub/docs URLs, features, testimonials, FAQ, pricing). Optional sections are gated by `enabled` flags (`testimonials.enabled`, `pricing.enabled`).

Design tokens (colors, fonts, the accent) live in `src/styles/global.css` under the `@theme` block.

## Develop

```bash
bun install
bun run dev        # http://localhost:4321
```

## Build & preview

```bash
bun run build      # static output → dist/
bun run preview    # preview the production build locally
```

## Deploy to Railway

This folder ships a `railway.toml`. Deploy it as **its own Railway service**:

1. New service → connect this repo.
2. Set **Root Directory** to `landing-page`.
3. Railway reads `railway.toml`: Railpack runs `bun run build`, then serves `dist/` with `sirv` on `$PORT`.

No Dockerfile needed - Railpack auto-detects the bun/Node project. Switch `builder` to `"DOCKERFILE"` in `railway.toml` only if you want a pinned nginx/caddy static serve.

> Remember to set the real origin in two places: `site` in `astro.config.mjs` and `site.url` in `src/config/landing.ts` (used for canonical + OG tags).

## Structure

```
src/
  config/landing.ts      # ← all copy & content (edit this)
  styles/global.css      # ← design tokens (@theme)
  layouts/Base.astro     # <head>, meta, OG/Twitter tags
  components/            # one component per page section
  pages/index.astro      # assembles the sections in order
public/favicon.svg
```

Sections, in order: Nav → Hero → TrustStrip → GetStarted → Features → Testimonials → Pricing → AskAi → Faq → FinalCta → Footer.

## WebMCP (agent-navigable page)

`src/components/WebMcp.astro` (loaded once from `Base.astro`) exposes this page's
actions to in-browser AI agents via the W3C [WebMCP](https://github.com/webmachinelearning/webmcp)
`navigator.modelContext` API, so an agent calls structured tools instead of
scraping the DOM. It registers four tools - `get_mcp_endpoint`,
`list_supported_clients`, `get_install_instructions`, `answer_faq` - all sourced
from `src/config/landing.ts`, so they never drift from the visible UI.

It's **progressive enhancement**: feature-detected and a no-op in browsers
without the API (everything except Edge 147+ / Chrome's origin trial as of
mid-2026), so normal visitors are unaffected and there's nothing to configure.

> WebMCP is an early, fast-moving W3C Draft. The component detects both
> `navigator.modelContext` (shipped) and `document.modelContext` (spec draft)
> and both the `provideContext`/`registerTool` registration shapes. Re-verify
> against the current spec before extending it.
