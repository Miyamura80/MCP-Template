/**
 * Support & contact: the self-serve troubleshooting and the human channels a
 * user reaches when something breaks or they have a question.
 *
 * Data-driven like the rest of the config: this one module feeds the `/support`
 * page, its `/help` and `/contact` aliases, the `/support.md` twin, and the
 * `## Support` section of llms.txt / agents.md / llms-full.txt. Point it at a
 * real inbox (see the TODO below) and it is live everywhere.
 *
 * Order is deliberate: troubleshooting first (most "issues" are setup, and an
 * agent or user should self-serve before opening a ticket), then the channels.
 */
import { site } from "./site";

// TODO: your real support inbox. This placeholder is intentionally not a live
// address - replace it (e.g. support@yourdomain.com) before you ship, or the
// "Email support" channel points nowhere.
export const supportEmail = "support@example.com";

export interface TroubleshootItem {
  /** Phrased as the symptom a user (or agent) would search for. */
  q: string;
  /**
   * Plain-text answer. NO markdown backticks or link syntax: this string
   * renders verbatim into the HTML page as well as into support.md, so markup
   * that survives to the browser shows up as literal characters.
   */
  a: string;
  /** Optional deep link the answer points at. */
  href?: string;
  hrefLabel?: string;
}

export interface SupportChannel {
  label: string;
  /** When to use this one, so a user picks the right door the first time. */
  description: string;
  href: string;
  /** Button/label text. */
  cta: string;
  /**
   * "public"  - anyone can read it (GitHub Issues): bugs, features, how-tos.
   * "private" - one-to-one (email): billing, account, anything with your data.
   */
  kind: "public" | "private";
}

export const support: {
  title: string;
  intro: string;
  troubleshooting: TroubleshootItem[];
  channelsHeading: string;
  channels: SupportChannel[];
} = {
  title: "Support & contact",
  intro: `Hit a snag or have a question? Most issues are setup or connection related and are covered below. If that does not sort it, reach a human through one of the channels underneath.`,
  // TODO: replace with the issues your users actually hit. These two cover the
  // failure modes common to any remote MCP server.
  troubleshooting: [
    {
      q: "The server will not connect, or my client rejects the URL",
      a: `${site.name} is a remote streamable-HTTP server, not a local stdio one. Add the endpoint ${site.mcpUrl} exactly, with no trailing path of your own. The server name is ${site.serverName}.`,
      href: "/#how-it-works",
      hrefLabel: "How it works",
    },
    {
      q: "Authentication fails, or my client keeps asking me to reconnect",
      a: `The MCP endpoint is an OAuth 2.1 resource server: your client completes consent in the browser (or you present an API key). If it loops, reconnect from your client, or revoke and re-grant access, then try again. See auth.md for the full recipe.`,
      href: "/auth.md",
      hrefLabel: "Agent auth (auth.md)",
    },
    {
      q: "The tools do not appear after I connect",
      a: `Once your client finishes auth, the tools register automatically - list them with the MCP tools/list method (most clients do this for you). If nothing shows, the connection did not complete: remove the server and add it again.`,
    },
  ],
  channelsHeading: "Still stuck? Talk to us",
  channels: [
    {
      label: "GitHub Issues",
      description:
        "Bugs, feature requests, and anything technical or reproducible. Public and searchable, so others hit by the same thing find the answer.",
      href: `${site.githubUrl}/issues`,
      cta: "Open an issue",
      kind: "public",
    },
    {
      label: "Email support",
      // TODO: keep this in step with `supportEmail` above.
      description:
        "Account, billing, security, or anything involving your data that should not be public. Goes straight to the team.",
      href: `mailto:${supportEmail}`,
      cta: supportEmail,
      kind: "private",
    },
  ],
};
