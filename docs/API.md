# API

Base path `/api/v1`. Interactive schema at `/docs` (disabled in production).

## Envelope

Every response uses one of three shapes:

```jsonc
{ "data": { ... } }                                  // single resource
{ "data": [ ... ], "meta": { ...pagination... } }    // collection
{ "error": { "code": "...", "message": "...",        // failure
             "details": { }, "request_id": "..." } }
```

`request_id` also comes back in the `X-Request-ID` header on every response, and appears in the
structured logs — quote it in a bug report and the exact request can be found.

## Decimals are strings

Monetary values, prices, quantities and ratios serialise as JSON **strings**:

```json
{ "net_pnl": "498.25", "r_multiple": "2.49", "entry_price": "50.0000000000" }
```

A JSON number is an IEEE double, which cannot represent `0.1` exactly. The frontend displays what
the backend computed; it never re-derives money from a lossy number.

`null` means **undefined**, not zero — see [FINANCIALS.md](FINANCIALS.md).

## Authentication

Session cookie, set by `POST /api/v1/auth/signup` or `/login`:

| Cookie | HttpOnly | Notes |
| --- | --- | --- |
| `tl_session` | yes | the credential |
| `tl_csrf` | no | copy into `X-CSRF-Token` on unsafe methods |

```
POST /api/v1/trades
Cookie: tl_session=...; tl_csrf=abc123
X-CSRF-Token: abc123
```

Requests without a valid session get `401`. Unsafe methods without a matching CSRF token get
`403 csrf_failed`.

## Errors

| Status | Code | Meaning |
| --- | --- | --- |
| 400 | `bad_request` | malformed request |
| 401 | `not_authenticated` / `session_expired` | sign in again |
| 401 | `invalid_credentials` | wrong email or password |
| 402 | `plan_limit_reached` | entitlement required; `details` names the feature and plan |
| 403 | `forbidden` | authenticated but not permitted |
| 403 | `csrf_failed` | missing or wrong CSRF token |
| 403 | `email_not_verified` | confirm the address first |
| 404 | `not_found` | missing **or belongs to another tenant** |
| 409 | `conflict` / `invalid_state` | uniqueness or lifecycle violation |
| 422 | `validation_error` | `details.fields[]` gives `{field, code, message}` |
| 429 | `rate_limited` | `Retry-After` header and `details.retry_after_seconds` |
| 5xx | `internal_error` / `upstream_unavailable` | generic; detail is in the logs |

Validation errors are field-addressable so a form can render them inline:

```json
{ "error": { "code": "validation_error", "message": "Some fields need attention.",
  "details": { "fields": [ { "field": "entry_price", "code": "greater_than",
                             "message": "Input should be greater than 0" } ] } } }
```

## Pagination

Page-based for UI tables:

```
GET /api/v1/trades?page=2&page_size=50&sort_by=net_pnl&sort_dir=desc
```

`page_size` is capped at 200. `meta` carries `page`, `page_size`, `total`, `total_pages`,
`has_next`.

## Filtering

The journal and analytics share one filter vocabulary; repeat a parameter to pass several values:

```
GET /api/v1/trades
  ?account_id=<uuid>&symbol=NVLX&symbol=ARBOR
  &direction=long&status=closed
  &strategy_id=<uuid>&setup_id=<uuid>&tag_id=<uuid>
  &session=new_york_am&asset_type=equity
  &date_from=2024-01-01T00:00:00Z&date_to=2024-06-30T23:59:59Z
  &outcome=winners&pnl_min=100&r_min=1&weekday=1&hour=14
  &search=breakout&has_notes=true
```

All filtering happens in SQL. `weekday` and `hour` are evaluated in the **account's** timezone.

## Long-running work

Backtests and large imports never block a request:

```
POST /api/v1/backtests/{id}/run     →  202 { "run_id", "job_id", "status": "queued" }
GET  /api/v1/backtests/jobs/{id}    →  { "status", "progress_percent", "error_message" }
GET  /api/v1/backtests/runs/{id}    →  full result once completed
```

## Endpoint map

| Prefix | Purpose |
| --- | --- |
| `/auth` | signup, login, logout, session, workspace switch, verification, password, sessions |
| `/users` | profile, data export, account deletion |
| `/organizations` | workspace settings and members |
| `/accounts` | accounts, cash ledger, snapshots, recalculation |
| `/instruments` | instrument catalogue and symbol aliases |
| `/market-data` | sources, coverage, candles, quality report |
| `/trades` | journal CRUD, fills, bulk operations, marks |
| `/orders`, `/positions` | executions and live exposure |
| `/strategies` | strategies, versions, and the executable engine registry |
| `/setups`, `/tags` | classification |
| `/journal-entries` | reviews and notes |
| `/imports` | upload → map → validate → preview → commit → revert |
| `/backtests` | configure, run, results, orders, compare, jobs |
| `/replay` | sessions, stepping, orders, protection |
| `/analytics` | overview, dashboard, comparison |
| `/files` | uploads, signed URLs, screenshots, usage |
| `/search` | global search |
| `/notifications` | list, unread count, mark read |
| `/billing` | plans, subscription, checkout, portal, webhook |
| `/admin` | overview, users, workspaces, jobs, failed imports, audit log |

## Health

`/health/live` never touches a dependency — a database blip must not cause the orchestrator to
kill healthy pods. `/health/ready` checks Postgres and Redis and returns 503 when the database is
unreachable; Redis being down reports `degraded` (reads still work).
