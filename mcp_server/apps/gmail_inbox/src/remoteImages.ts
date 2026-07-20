/**
 * Remote-image blocking for email HTML rendered inside the app iframe.
 *
 * Strict-CSP hosts (claude.ai enforces `img-src 'self' data: blob:
 * https://assets.claude.ai`) silently block every image an email references
 * from a sender-controlled origin, while lax hosts load them from the
 * network. We normalize both worlds to classic email-client behavior:
 * remote images are stripped at render time and a "Show images" action
 * fetches them through the server (`gmail_inbox.fetch_image`) and swaps in
 * `data:` URIs, which every host's CSP allows. This also stops tracking
 * pixels from firing without user intent - even on lax hosts.
 *
 * Both functions operate on ALREADY-SANITIZED HTML (see sanitize.ts); they
 * must run after DOMPurify so the marker attributes they add survive.
 */

/** 1x1 transparent GIF shown in place of a blocked remote image. */
export const BLOCKED_IMG_PLACEHOLDER =
  "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";

const REMOTE_URL_RE = /^https?:\/\//i;

export type BlockedHtml = {
  /** HTML with remote image references neutralized + marker attributes. */
  html: string;
  /** Unique remote URLs that were blocked, in document order. */
  remoteUrls: string[];
};

/**
 * Neutralize every remote image reference in sanitized email HTML.
 *
 * Handles the three ways email HTML loads images: `<img src>` (plus
 * `srcset`), the legacy `background` attribute on tables/cells, and inline
 * `style` background images. Blocked `src`/`background` URLs are stashed in
 * `data-remote-src` / `data-remote-background` so `restoreRemoteImages` can
 * put fetched bytes back; `srcset` and CSS backgrounds are dropped outright
 * (the plain `src` swap covers what emails actually rely on).
 */
export function blockRemoteImages(html: string): BlockedHtml {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const urls: string[] = [];
  const seen = new Set<string>();
  const note = (url: string) => {
    if (!seen.has(url)) {
      seen.add(url);
      urls.push(url);
    }
  };

  for (const img of Array.from(doc.querySelectorAll("img"))) {
    const src = img.getAttribute("src") || "";
    if (REMOTE_URL_RE.test(src)) {
      img.setAttribute("data-remote-src", src);
      img.setAttribute("src", BLOCKED_IMG_PLACEHOLDER);
      note(src);
    }
    if (REMOTE_URL_RE.test(img.getAttribute("srcset") || "")) {
      img.removeAttribute("srcset");
    }
  }

  for (const el of Array.from(doc.querySelectorAll("[background]"))) {
    const bg = el.getAttribute("background") || "";
    if (REMOTE_URL_RE.test(bg)) {
      el.setAttribute("data-remote-background", bg);
      el.removeAttribute("background");
      note(bg);
    }
  }

  for (const el of Array.from(doc.querySelectorAll<HTMLElement>("[style]"))) {
    const style = el.getAttribute("style") || "";
    if (/url\(\s*['"]?https?:\/\//i.test(style)) {
      el.setAttribute(
        "style",
        style.replace(/url\(\s*['"]?https?:\/\/[^)]*\)/gi, "none"),
      );
    }
  }

  return { html: doc.body.innerHTML, remoteUrls: urls };
}

/**
 * Swap blocked references back in as fetched `data:` URIs.
 *
 * `resolved` maps original remote URL -> data URI. URLs missing from the map
 * (fetch failed / oversized) keep their placeholder.
 */
export function restoreRemoteImages(
  blockedHtml: string,
  resolved: Map<string, string>,
): string {
  const doc = new DOMParser().parseFromString(blockedHtml, "text/html");

  for (const img of Array.from(doc.querySelectorAll("[data-remote-src]"))) {
    const dataUri = resolved.get(img.getAttribute("data-remote-src") || "");
    if (dataUri) {
      img.setAttribute("src", dataUri);
      img.removeAttribute("data-remote-src");
    }
  }

  for (const el of Array.from(
    doc.querySelectorAll("[data-remote-background]"),
  )) {
    const dataUri = resolved.get(
      el.getAttribute("data-remote-background") || "",
    );
    if (dataUri) {
      el.setAttribute("background", dataUri);
      el.removeAttribute("data-remote-background");
    }
  }

  return doc.body.innerHTML;
}
