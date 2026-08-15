<!-- UNWRITTEN-PLACEHOLDER -->

# Privacy Policy

**This document has not been written yet.**

It is a placeholder shipped with the repository so the page, the consent checkbox and the
acceptance record all work end to end. It is not a privacy policy, and it makes no promises.

Replace this file with a policy written or reviewed by a lawyer, then delete the marker on the
first line. Tradeloom refuses to boot in production while it is still present.

A policy for this application has to describe, accurately, what the code already does:

- **What is collected.** Account details (email, name, timezone), everything a user records or
  imports about their trading, uploaded files, and session and audit records including IP address
  and user agent.
- **Why.** To provide the service, to secure accounts, and to bill — if billing is enabled.
- **Who else sees it.** Any processor the deployment enables: an email provider, an object store,
  Stripe if billing is on, and an error reporter if `SENTRY_DSN` is set. Error reports carry a
  user id, never an email address, and secrets are scrubbed before they leave.
- **How long it is kept.** See the retention table in `docs/DEPLOYMENT.md`, which reflects what
  the scheduled jobs actually delete.
- **What a user can do.** Export everything (Settings → Data), and request deletion, which runs
  after a seven-day grace period and anonymises rather than orphans the audit trail.
- Rights under whichever regimes apply to you and your users, and how someone exercises them.

Bump `VERSIONS["privacy"]` in `backend/tradeloom/core/legal.py` whenever the text changes.
