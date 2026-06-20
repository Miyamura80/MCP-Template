# Publishing to the ChatGPT App Directory

Checklist for submitting the `gmailmcp` MCP server
(`https://mcp.gmailmcp.com/mcp`) to the ChatGPT app directory via the OpenAI
Apps SDK. Verified against OpenAI docs as of 2026-06.

> The app **must** be submitted and approved to appear in the directory — there
> is no automatic listing. Approved apps roll out to users (directory + in-chat
> suggestions).

```
 Build MCP server ──► Test in ChatGPT Developer Mode ──► Submit for review
   (Apps SDK)            (private connector)               (dashboard form)
                                                                │
                                                                ▼
                                          OpenAI review ──► Approved ──► Listed
                                                                │
                                                                └─► Rejected ──► fix & resubmit
```

## 1. Prerequisites

- [ ] MCP server on public HTTPS exposing `/mcp` — ✅ `https://mcp.gmailmcp.com/mcp`
      (ngrok/Cloudflare Tunnel is only OK for dev testing, **not** submission).
- [ ] Apps SDK-style tools defined (tools, schemas, optional iframe widget/UI).
- [ ] Published **privacy policy URL** (see §4).
- [ ] Company / landing URL.
- [ ] App name, logo, screenshots.
- [ ] **Demo account** with sample data, reachable with just a login (see §5).

## 2. Build + test in Developer Mode

1. [ ] Enable Developer Mode (workspace admin):
       *Workspace Settings → Permissions & Roles → Connected Data →
       Developer mode / Create custom MCP connectors*.
2. [ ] Add connector: *ChatGPT → Settings → Connectors → Create*, point at
       `https://mcp.gmailmcp.com/mcp`.
3. [ ] Exercise in real chats — direct, indirect, and negative prompts; watch
       server logs; debug any widget with browser devtools.
4. [ ] After tool/description changes: redeploy, then **Refresh** the connector.

## 3. Submission form fields

Complete every field (placeholders are rejected), check all confirmation boxes,
then **Submit for review**. You will get a confirmation email with a **Case ID**.

| Field | Value for `gmailmcp` |
|---|---|
| App name | _TBD_ |
| Logo | _TBD (asset)_ |
| Description | What it does / when to use it |
| Company URL | _TBD_ |
| Privacy policy URL | _TBD — must be live (see §4)_ |
| **MCP server URL** | `https://mcp.gmailmcp.com/mcp` (real, reachable — no placeholder) |
| Tool information | The MCP tools exposed by the server |
| Screenshots | App in action |
| Test prompts + expected responses | How reviewers should exercise it |
| **Demo/test credentials** | Pre-connected demo Gmail account (see §5) |
| Localization / country availability | Languages + regions |

## 4. Privacy policy requirements

Must be **published** and cover, at minimum:

- [ ] Categories of personal data collected
- [ ] Purposes of use
- [ ] Categories of recipients
- [ ] Data retention timelines
- [ ] User controls offered

> A Gmail app handles Google user data, so this will be scrutinized closely.

## 5. Authentication / demo account (most common rejection cause)

`mcp.gmailmcp.com/mcp` is **OAuth-protected** — unauthenticated `initialize`
returns `401` with a `www-authenticate: Bearer` challenge to `authkit.app`.
OpenAI's review team must be able to connect, so:

- [ ] Provide a **fully-featured demo Gmail account with sample mail**.
- [ ] Reviewer can log in with **only** the supplied credentials — no new
      sign-up, no 2FA on an inaccessible account (both cause rejection).
- [ ] OAuth flow is transparent; permissions requested are **minimal** and
      clearly disclosed.

Common rejection message:
*"We're unable to connect to your MCP server using the MCP URL and/or test
credentials we were given."*

## 6. Quality bar

- [ ] Server reachable over HTTPS; MCP handshake works with supplied creds.
- [ ] Transparent, minimal-scope auth.
- [ ] Clear privacy policy.
- Meeting baseline guidelines → **listed**. Meeting the higher **design
  guidelines** bar → eligible to be **featured** more prominently.

## 7. After approval / maintaining

- [ ] To ship updates: redeploy, then **Refresh** the connector to update
      metadata. Significant changes may require re-review.
- [ ] Verify the live build via the public health endpoint:

  ```bash
  curl -s https://mcp.gmailmcp.com/health | jq '.version'
  ```

  (Currently reports `0.1.1`.)

## Sources

- [Submit and maintain your app – Apps SDK](https://developers.openai.com/apps-sdk/deploy/submission)
- [App submission guidelines – Apps SDK](https://developers.openai.com/apps-sdk/app-submission-guidelines)
- [Submitting apps to the ChatGPT app directory – OpenAI Help Center](https://help.openai.com/en/articles/20001040)
- [Connect from ChatGPT – Apps SDK](https://developers.openai.com/apps-sdk/deploy/connect-chatgpt)
- [Developer mode and MCP apps in ChatGPT – OpenAI Help Center](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt-beta)
- [Test your integration – Apps SDK](https://developers.openai.com/apps-sdk/deploy/testing)
- [Optimize Metadata – Apps SDK](https://developers.openai.com/apps-sdk/guides/optimize-metadata)
- [Developers can now submit apps to ChatGPT – OpenAI](https://openai.com/index/developers-can-now-submit-apps-to-chatgpt/)
