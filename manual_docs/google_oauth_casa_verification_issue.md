---
name: Tracking Issue
about: Google OAuth publishing + restricted-scope (CASA) verification
labels: enhancement, ops, blocked-external
title: "Publish Google OAuth app: restricted-scope (gmail.modify) verification + CASA"
---

## Problem

Our Google OAuth client requests the **restricted** scope
`https://www.googleapis.com/auth/gmail.modify` (see
[`services/gmail_svc.py`](../services/gmail_svc.py) → `GMAIL_SCOPES`). Until the
app is verified and **published to production**, we are stuck in Google's
"Testing" mode, which has two blocking consequences for this codebase:

- **100-user cap** on accounts that can link Gmail.
- **Refresh tokens expire after 7 days** for sensitive/restricted scopes in
  Testing mode. We persist long-lived refresh tokens
  (`db.models.google_tokens.refresh_token_enc`) and mint access tokens off them
  (`gmail_svc.py:_mint_access_token`). In Testing, **every linked account
  silently breaks weekly.** Publishing is what makes stored refresh tokens
  durable.

This is a **separate track** from our MCP OAuth (WorkOS AuthKit, see
[`docs/content/docs/mcp/oauth.mdx`](../docs/content/docs/mcp/oauth.mdx)). AuthKit
is the authorization server for `/mcp`; the Google client (`GOOGLE_CLIENT_ID`)
is its own thing in Google Cloud Console and needs its own verification.

## Key finding: AI pentesters do NOT reduce the cost

We evaluated whether AI pentesting tools (XBOW, Aikido, etc.) could let us skip
or cheapen the assessment. **They cannot.** The cost and time of CASA come from
**compliance/certification**, not from the technical scanning:

- CASA Tier 2 = (a) a DAST scan + (b) a **Letter of Validation (LOV)**.
- **(a) the scan is already free** - Google's recommended tool is OWASP ZAP
  ($0). Running XBOW here makes it *more* expensive, not less.
- **(b) the LOV is the real, mandatory cost** and **only an App Defense
  Alliance authorized lab can issue it** (TAC Security, DEKRA, Leviathan,
  Prescient, …). An AI pentest report has **zero standing** with Google's OAuth
  team regardless of quality. No AI can substitute for the lab.
- The annual re-certification fee (one authorized-lab fee per year) is an
  **unavoidable floor**. No tooling removes it.

Where AI *does* help (but doesn't cut the base cost): **pre-remediation** before
the official assessment, so we pass on the first pass and avoid re-scan
fees/delays. Free OWASP ZAP + an LLM to read findings and draft the SAQ covers
the same ground at $0; we don't need a paid AI pentester for it.

## The two gates (in order)

```
  GATE 1 - Consent screen / Brand verification   (~2-3 business days)
    • App audience: Testing → In production
    • Verified privacy policy, homepage, ToS (all on domain-verified hosts)
    • Scope justification + demo video (OAuth flow + each gmail.modify use)
    • Submit for verification in Google Cloud Console
                              │ restricted scope present →
                              ▼
  GATE 2 - CASA Tier 2 security assessment        (weeks → months)
    • Triggered by Google email ("your app is in scope")
    • Authorized ADA lab (TAC Security / DEKRA / Leviathan / Prescient)
    • DAST scan: no high/medium-likelihood CWE findings
    • ~54-question SAQ + encryption / data-handling evidence
    • Lab issues Letter of Validation → Google
    • Re-validate every 12 months from LOV effective date
```

## Expected cost & time

| Item | Time | Cost |
|---|---|---|
| Gate 1 - brand/consent verification | ~2–3 business days (longer if branding changed) | $0 |
| Public pages + domain verification (homepage, privacy policy w/ Limited Use, ToS) | Dev time | $0 (uses existing deploy domains) |
| Demo video (consent + each restricted action) | Dev time | $0 |
| Gate 2 - DAST scan (OWASP ZAP, self-run) | Dev time | **$0** |
| Gate 2 - **CASA Tier 2 Letter of Validation (authorized lab)** | **Several weeks to a few months** | **~$few-hundred to ~$1k (TAC "Lab Scan", cheapest) up to $15k–$75k (full third-party assessor)** |
| **Annual re-certification** | Recurring, every 12 months | Same lab fee, recurring |

**Realistic cheapest path:** self-run OWASP ZAP ($0) → fix findings → TAC
Security lab validation (Google's preferred/cheapest lab). Budget for the
recurring annual fee - that's the true, unavoidable cost.

> ⚠️ CASA rules shift often. The Tier 2 "self-scan" flow was recently deprecated
> in favor of a lab-initiated process. Confirm current scan-tool requirements in
> the notification Google sends before paying any vendor.

## Proposed next steps (checklist)

- [ ] Confirm the Google client's current publishing status (Testing vs. submitted) in Cloud Console → APIs & Services → OAuth consent screen.
- [ ] Keep scopes minimal - `gmail.modify` only; do **not** add `readonly`/`compose`/`send` (redundant scopes get rejected). See note at `gmail_svc.py:56`.
- [ ] Stand up + domain-verify homepage, privacy policy (explicit **Limited Use** statement for Gmail data), and ToS on the deploy domain.
- [ ] Record the demo video (consent grant + read/draft/send/modify).
- [ ] Submit Gate 1.
- [ ] On Google's CASA notification: run OWASP ZAP, remediate, select an authorized lab, complete the SAQ, obtain the LOV.
- [ ] Document the data-handling evidence for the SAQ (Fernet token encryption in `common/token_encryption.py`, HMAC-signed OAuth state in `gmail_svc.py`, TLS-only token exchange, encrypted refresh tokens at rest).
- [ ] Set a calendar reminder for annual re-certification (12 months from LOV).
- [ ] Fix the dangling doc reference in `gmail_svc.py:59` ("See docs/.../oauth verification notes" - no such doc exists yet).

## References

- [Restricted scope verification - Google for Developers](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)
- [CASA Tier 2 Process - App Defense Alliance](https://appdefensealliance.dev/casa/tier-2/tier2-overview)
- [Application Scanning Guide - App Defense Alliance](https://appdefensealliance.dev/casa/tier-2/ast-guide)
- [CASA Tier 2/3 Providers, Pricing & Cheapest Option - Switch Labs](https://www.switchlabs.dev/post/casa-tier-2-tier-3-security-review-providers-pricing-and-the-cheapest-option)
- [How We Passed Google CASA Tier 2 With Claude - Orbis](https://meetorbis.com/blog/how-we-passed-google-casa-tier-2-with-claude)
</content>
</invoke>
