# Security

## Threat model

Tradeloom stores a trader's complete position history — commercially sensitive, and a target for
anyone wanting to front-run or simply embarrass a user. The threats taken seriously here are:

1. **Cross-tenant data access** (the big one for multi-tenant SaaS)
2. Session theft and fixation
3. Credential stuffing and brute force
4. Privilege escalation to admin
5. Malicious uploads
6. Billing manipulation

## Tenant isolation

The central mechanism: every tenant-owned read and write goes through `TenantRepository`, which
is constructed with an `organization_id` and injects `WHERE organization_id = :org` into every
statement. **A service never writes that clause itself, so it cannot forget it.**

The active workspace is resolved from the **session record**, never from the request:

```python
organization_id = principal.session_record.active_organization_id
```

There is no endpoint that accepts an organization id and acts on it. Switching workspace is a
dedicated endpoint that verifies membership server-side and rewrites the session.

### 404, not 403

Cross-tenant access returns **404**. A 403 would confirm that the resource exists, letting an
attacker enumerate ids and map another tenant's data without ever reading it. This is asserted
across accounts, trades, files, imports, strategies, backtests, runs, jobs and replays in
`tests/test_tenant_isolation.py`.

Bulk operations resolve ids through the same tenant repository, so a mixed list silently drops
the foreign ids and reports the true success count.

## Authentication

- **Argon2id** password hashing, OWASP-2024 defaults (64 MiB, t=3, p=2), configurable.
  `check_needs_rehash` transparently upgrades cost parameters on the next successful sign-in.
- Passwords: minimum 12 characters, at least three of four character classes, at least six
  distinct characters, and a small deny-list. Length does the heavy lifting.
- **Timing equalisation**: an unknown email still burns an Argon2 verification
  (`dummy_verify`), so response time does not reveal whether an account exists.
- Login and password reset return **identical** responses whether or not the account exists.
  Signup necessarily returns 409 on a duplicate — that is the one unavoidable oracle, and it is
  rate-limited.

### Lockout

Failed attempts are counted per user and persisted. Past the threshold (default 8) the account is
locked for a cooling-off period (default 15 minutes).

The counters are **committed explicitly before the 401 propagates**. Without that, the
request-scoped session would roll back on the exception and discard the very counters the lockout
depends on — a bug this codebase had and now has a test for.

## Sessions

- Opaque 256-bit token in an **HttpOnly, Secure, SameSite=Lax** cookie.
- The server stores only the **SHA-256 digest**, so a database dump cannot be replayed as a live
  session.
- Three clocks: absolute expiry (14 d), idle timeout (24 h), rotation interval (1 h). Past the
  rotation interval the token is regenerated and the cookie replaced, limiting the value of a
  stolen cookie.
- **Signing in always creates a new session id** — no fixation.
- Changing a password revokes every other session; a reset revokes all of them.
- Users can list and revoke their own sessions.

## CSRF

Double-submit with two deliberately different cookies:

| Cookie | HttpOnly | Purpose |
| --- | --- | --- |
| `tl_session` | **yes** | the credential; JavaScript must never read it |
| `tl_csrf` | no | copied by the client into `X-CSRF-Token` |

`SameSite=Lax` blocks the cross-site POST case outright; the token covers the remainder. Every
unsafe method (`POST`/`PUT`/`PATCH`/`DELETE`) requires a header matching the session's token,
compared in constant time.

The Stripe webhook is the one exception: it authenticates with Stripe's HMAC signature over the
raw body, not a session.

## Authorization

Two independent axes:

- **Platform role** (`user` / `support` / `admin`) — gates `/api/v1/admin/*` via the `AdminUser`
  dependency on every route. No client-supplied flag exists anywhere in that module.
- **Workspace role** (`viewer` < `member` < `manager` < `owner`) — gates writes and management.

The profile update schema uses `extra="forbid"`, so a request trying to set `role` is rejected
with 422 rather than ignored.

## Rate limiting

Fixed-window, Redis-backed with an in-process fallback. A Redis outage degrades to per-process
limits — still limiting, which is the correct failure mode for a limiter — rather than failing
open. Tighter named limits apply to login, signup and password reset, keyed per bucket so a burst
against one cannot exhaust another's allowance.

## Uploads

Three checks before a byte reaches storage:

1. **Size** against `UPLOAD_MAX_BYTES`.
2. **Declared MIME** against an allow-list.
3. **Magic bytes** against the declared type — a `.png` that is really an HTML document is
   rejected. Text formats are validated by a UTF-8 decode.

Object keys are server-generated and tenant-namespaced (`org/<org_id>/<purpose>/<uuid><ext>`).
The client's filename is stored for display only and never influences the key.

Objects are **private**. Access is always a short-lived signed URL minted *after* an ownership
re-check — possession of a file id is never sufficient.

## Secrets and logging

Never logged: passwords, password hashes, session tokens, CSRF tokens, OAuth secrets, Stripe
keys, signed URLs, cookies, authorization headers. A structlog processor redacts these keys by
name regardless of where they appear in an event.

- OAuth **access and refresh tokens are not persisted at all** — OAuth is used for identity only.
- Stripe webhook payloads are stored as a redacted subset, never the full object, which can carry
  PII.
- Job records separate `error_message` (user-safe) from `error_detail` (diagnostics). Only admins
  see the latter; it is excluded from `JobService.to_dict`.
- Constraint names and stack traces never reach an API response — the user gets a generic message
  and the detail goes to the log with a request id.

## Billing integrity

The browser is never the source of truth. Checkout returns a Stripe URL; the plan changes only
when a **signature-verified webhook** says so. No endpoint accepts a plan from the client.

Webhook idempotency is enforced by a unique constraint on `subscription_events.external_event_id`,
so a redelivery is recognised and skipped rather than applied twice.

Entitlements are resolved server-side on every enforcement point. The client receives a snapshot
purely so it can grey out unavailable controls; a modified browser payload buys nothing.

## Injection

All queries are SQLAlchemy Core/ORM constructs with bound parameters. There is no string-built
SQL anywhere. Enum-typed columns validate on write, so a crafted status value is rejected before
it reaches the database.

The API serves JSON only and sets a restrictive CSP, `nosniff`, `X-Frame-Options: DENY` and
`Referrer-Policy`. HSTS is sent **only** when `COOKIE_SECURE` is true — emitting it from a plain
HTTP dev server would poison the developer's browser for the whole domain.

## Auditing

`audit_logs` is append-only (no `updated_at`, no update path) and records actor, action, entity,
field-level diff, IP, user agent and request id. Sensitive fields are redacted at capture, so a
value cannot reach the table by being passed through a generic `changes` dict. `actor_email` is
denormalised so the log stays meaningful after a user is erased.

## Data protection

- **Export**: `GET /api/v1/users/me/export` returns the workspace's complete data as JSON.
- **Deletion**: requires the password plus a typed confirmation, then a 7-day grace period during
  which signing in cancels it. After that the retention job deletes the workspaces the user
  solely owns (cascading to all trading data) and anonymises the user row — kept rather than
  dropped so the append-only audit log does not develop holes.

## Reporting

Report vulnerabilities privately to the maintainers rather than opening a public issue.
