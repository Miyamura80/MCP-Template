/**
 * Sanitize email HTML with remote images blocked (or resolved) in the SAME
 * DOMPurify pass.
 *
 * Why one pass matters: DOMPurify's safety guarantee applies to the exact
 * string it emits. Any later parse -> mutate -> serialize step (the mXSS
 * problem class) would make us the last writer instead of the sanitizer, so
 * the blocking runs inside an `afterSanitizeAttributes` hook and the string
 * handed to `dangerouslySetInnerHTML` is byte-for-byte DOMPurify output.
 *
 * Why blocking at all: strict-CSP hosts (claude.ai enforces `img-src 'self'
 * data: blob: https://assets.claude.ai` on the app iframe) block every image
 * an email references from a sender-controlled origin, while lax hosts
 * (Goose) load them straight from the network. We normalize both to classic
 * email-client behavior: remote references are stripped at render time and a
 * "Show images" action fetches them through the server
 * (`gmail_inbox.fetch_image`) and re-renders with `data:` URIs, which every
 * host CSP allows. This also stops tracking pixels firing without user
 * intent on lax hosts.
 *
 * The blocking is DEFAULT-DENY, not an enumerated blocklist: on every
 * element, every URL-bearing attribute DOMPurify's HTML profile can admit
 * (`src`, `poster`, `background`, `srcset`, plus `url()` in inline styles)
 * keeps only `data:`/`cid:` values. Everything else - absolute http(s),
 * protocol-relative `//host/...`, relative paths, unknown schemes - is
 * stripped, so a new URL-bearing attribute admitted by a future DOMPurify
 * profile change is blocked here for free instead of becoming a bypass.
 */

import DOMPurify from "dompurify";
import { EMAIL_SANITIZE_CONFIG } from "./sanitize";

/** 1x1 transparent GIF shown in place of a blocked remote image. */
export const BLOCKED_IMG_PLACEHOLDER =
  "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";

/** Values that never leave the document and are allowed by every host CSP. */
const SAFE_INLINE_RE = /^(data|cid):/i;

/** Absolute or protocol-relative remote reference - fetchable via the proxy. */
const REMOTE_RE = /^(https?:)?\/\//i;

/**
 * Attributes that trigger a fetch when the HTML renders. `srcset` is handled
 * separately (multi-URL syntax; always stripped, `src` covers restore).
 */
const URL_ATTRS = ["src", "poster", "background"] as const;

/** Strip every non-data `url(...)` from an inline style. */
const STYLE_URL_RE = /url\(\s*(?!['"]?data:)[^)]*\)/gi;

export type SanitizedEmailHtml = {
  /** Sanitized HTML, remote references blocked or swapped to data: URIs. */
  html: string;
  /**
   * Unique remote URLs found (protocol-relative normalized to https:), in
   * document order - both still-blocked and already-resolved ones, so
   * callers can diff against `resolved` to know what remains hidden.
   */
  remoteUrls: string[];
};

/**
 * Sanitize untrusted email HTML, blocking remote image references.
 *
 * `resolved` maps remote URL -> `data:` URI previously fetched through the
 * server proxy; matching references are swapped in instead of blocked. Call
 * again with a bigger map to reveal more images - the transform always
 * starts from the original untrusted HTML, never from its own output.
 */
export function sanitizeEmailHtml(
  html: string,
  resolved?: Map<string, string> | null,
): SanitizedEmailHtml {
  const remoteUrls: string[] = [];
  const seen = new Set<string>();

  const hook = (node: Element) => {
    for (const attr of URL_ATTRS) {
      if (!node.hasAttribute(attr)) continue;
      const value = (node.getAttribute(attr) || "").trim();
      if (!value || SAFE_INLINE_RE.test(value)) continue;
      if (REMOTE_RE.test(value)) {
        const url = value.startsWith("//") ? `https:${value}` : value;
        if (!seen.has(url)) {
          seen.add(url);
          remoteUrls.push(url);
        }
        // This hook runs after DOMPurify's own attribute checks, so writes
        // here bypass its URI-scheme validation - accept nothing but the
        // data:image/* URIs the fetch flow produces.
        const dataUri = resolved?.get(url);
        if (dataUri && /^data:image\//i.test(dataUri)) {
          node.setAttribute(attr, dataUri);
          continue;
        }
      }
      // Default-deny: unresolved remote, relative path, or unknown scheme.
      if (attr === "src" && node.tagName === "IMG") {
        node.setAttribute("src", BLOCKED_IMG_PLACEHOLDER);
      } else {
        node.removeAttribute(attr);
      }
    }
    // srcset can carry many URLs (and lives on <source> inside <picture>,
    // where the browser prefers it over the blocked <img src>). Always drop
    // it; the plain src swap covers everything emails actually rely on.
    if (node.hasAttribute("srcset")) node.removeAttribute("srcset");

    const style = node.getAttribute("style");
    if (style && /url\(/i.test(style)) {
      node.setAttribute("style", style.replace(STYLE_URL_RE, "none"));
    }
  };

  DOMPurify.addHook("afterSanitizeAttributes", hook);
  try {
    return {
      html: DOMPurify.sanitize(html, EMAIL_SANITIZE_CONFIG),
      remoteUrls,
    };
  } finally {
    DOMPurify.removeHook("afterSanitizeAttributes");
  }
}
