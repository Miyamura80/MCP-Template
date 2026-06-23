---
description: Decide allowlist vs CLOUD_SANDBOX_IGNORES when a link 403s under the Claude Code cloud egress proxy
globs:
  - "scripts/lint_links.py"
---

# lint_links: allowlist vs CLOUD_SANDBOX_IGNORES

`make lint_links` does live `GET`s against every external URL in the repo's
markdown. In the Claude Code web sandbox, outbound HTTP goes through an
**egress allowlist proxy that returns `403` for any non-allowlisted host**, so
a `403` here almost never means a dead link; it means "not on the allowlist."
Confirm with `curl -s -o /dev/null -w "%{http_code}" <url>` before treating a
failure as real; a genuine break is usually a `404`.

There are three levers. Pick by the **data-exfiltration** question below, not by
how trustworthy the site is. Allowlisting a host opens it to *every* process in
the sandbox (a prompt-injected agent, a malicious dependency), not just the
linter.

## The exfil test

> Can an attacker push data to this host and **read it back**, or make the host
> **relay a request onward** to a URL they choose?

Discount two things when answering: the host itself being compromised, and
"blind" logging, where data lands in server logs the attacker cannot read (e.g.
`GET /?secret=...`). Blind-log-only is *not* a usable exfil channel.

- **No** (static docs/marketing, only reachable via plain `GET`): it is safe to
  grant standing egress. **Allowlist it** in the environment's **Network access
  -> Custom** field (keep "include default package managers" checked). This
  lives in environment config, **not** in this script, and makes the link pass
  in both the sandbox and CI.
- **Yes** (programmable endpoint): **do not allowlist.** Add a pattern to
  `CLOUD_SANDBOX_IGNORES` so it is skipped in the sandbox only. CI (open egress)
  still checks it for real. Programmable means SSRF/relay (e.g. `shields.io`'s
  dynamic-badge `?url=` fetch), RPC endpoints (on-chain write, public read),
  package/tool registries (publish-then-read), render-and-share services
  (carbon, chalk), live MCP/payment `POST` endpoints, publish surfaces (VS Code
  Marketplace), or platform apexes that carry APIs/webhooks beyond the one link.

## When a whole file is the problem

If a doc cites many third-party hosts that are each only a citation (e.g. a
research doc with ~20 news/vendor links), do not allowlist all of them and do
not add 20 ignore patterns. Add the file to `CLOUD_SANDBOX_IGNORE_FILES`. It is
skipped in the sandbox `find`; CI still link-checks it.

## Mechanics

- Everything sandbox-specific is gated on `_in_claude_code_cloud()`
  (`CLAUDE_CODE_REMOTE=true`). Never let a skip leak into local runs or GitHub
  Actions CI, which have open egress and must check every link.
- Ignore patterns are matched with `re.match` (start-anchored, not end). End
  host patterns with `(/|\?|$)` so `shields.io` does not also match
  `shields.io.evil.com`.
- After moving a host to the allowlist, confirm it is actually still linked
  somewhere outside any ignored file. Do not allowlist hosts that only appeared
  via a file now in `CLOUD_SANDBOX_IGNORE_FILES`.
