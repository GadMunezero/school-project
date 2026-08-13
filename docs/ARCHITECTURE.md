# Architecture

## Shape of the system

```
                    ┌──────────────┐
   browser ───────► │   frontend   │  Next.js (App Router)
                    └──────┬───────┘
                           │  HTTP + session cookie
                    ┌──────▼───────┐
                    │     API      │  FastAPI  — request handling only
                    │  ┌────────┐  │
                    │  │services│  │  business logic and every financial rule
                    │  ├────────┤  │
                    │  │repos   │  │  tenant-scoped data access
                    │  └────────┘  │
                    └──┬────────┬──┘
                       │        │
              ┌────────▼──┐  ┌──▼──────┐   ┌──────────────┐
              │ Postgres  │  │  Redis  │◄──┤ Celery worker│
              └───────────┘  └─────────┘   └──────┬───────┘
                       ▲                          │
                       └──────────────────────────┘
                    ┌──────────────┐
                    │ S3 / MinIO   │  screenshots, imports, exports
                    └──────────────┘
```

`tradeloom.engine` sits beside all of this and imports none of it.

## Layering rules

```
api  →  services  →  repositories  →  models
                  ↘  engine (pure)
```

1. **`api/` contains no business logic.** A route parses input, calls one service, and
   serialises the result. If a handler starts branching on domain state, that logic belongs in a
   service.
2. **`services/` is the only place financial rules live.** The backend is authoritative: the
   frontend never computes P&L, an R multiple, or a win rate. It renders numbers the API sent.
3. **`repositories/` is the only place tenant filtering happens.** See
   [SECURITY.md](SECURITY.md).
4. **`engine/` imports nothing from the rest of the application** — no models, no session, no
   settings beyond the pure money helpers. That is enforceable by reading its imports, and it is
   what lets the engine be tested in milliseconds.
5. **Services never import from `api/`.** A service must be callable from a Celery task, the CLI
   or a test with no HTTP involved — which is exactly how the worker uses them.

## Request lifecycle

1. `RequestContextMiddleware` assigns a request id and binds logging context.
2. `RateLimitMiddleware` applies the global per-IP limit.
3. The `tenant_context` dependency turns the session cookie into a `TenantContext`:
   authenticated user, active organization, membership role. **The active workspace comes from
   the session record, never from the request.**
4. The handler calls a service constructed with `(session, organization_id)`.
5. The service does its work through repositories bound to that organization.
6. The handler commits and serialises.
7. Errors become the single envelope in `api/errors.py`.

## Tenancy

An **organization** (workspace) owns all trading data. Every user gets a personal organization at
signup and may belong to others.

Every tenant-owned table carries `organization_id`, and `TenantRepository` injects
`WHERE organization_id = :org` into every statement. A service never writes that clause itself,
so it cannot forget it. Switching workspace is a dedicated endpoint that verifies membership and
rewrites the session server-side.

## Background work

No HTTP request waits for a long operation. Submitting a backtest validates everything, writes a
`backtest_runs` row and a `job_records` row, returns a job id, and dispatches to Celery *after*
the commit — so a worker can never pick up a job whose row is not yet visible.

`JobRecord` is the durable status; Celery's result backend is treated as ephemeral. If the broker
is down at dispatch time the job stays `queued` and is picked up on recovery; the API says so
rather than pretending the run started.

## Where the money maths lives

One implementation, three callers:

| Caller | Path |
| --- | --- |
| Manual trade entry | `TradeService.ingest_fills` → `position_builder.build_trades` |
| CSV import | `ImportPipeline.commit` → `TradeService.ingest_fills` → same |
| Backtest engine | `engine/portfolio.py` (same weighted-average rules, no DB) |

Journal analytics map trades onto the engine's `SimTrade` shape and run them through the *same*
`PerformanceAnalyzer` the backtester uses, so a win rate means the same thing on the dashboard as
it does in a backtest report.

## The frontend

The client is a Next.js App Router application under `frontend/`. It is a rendering layer: it
holds no financial logic, and the four rules below are what keep it that way.

**1. Money crosses the wire as strings, and stays a string.**
The API serialises every `Decimal` as a JSON string. `src/lib/types.ts` types those fields as
`DecimalString = string | null | undefined`, so `trade.net_pnl * 2` is a compile error rather than
a silent precision loss. `src/lib/format.ts` formats those strings *textually* — grouping digits
and rounding by propagating a carry through the characters — because `Number("1.005").toFixed(2)`
returns `"1.00"`. The single documented exception is `toChartNumber`, used only for canvas pixel
positions; it is named so that its use is visible in review. `src/lib/format.test.ts` covers the
cases a double would get wrong.

**2. `null` renders as an em dash, never as zero.**
An undefined profit factor (no losing trades) and a profit factor of zero are different claims.
Every formatter returns `"—"` for `null`, and `safe_div` on the server returns `None` rather than
`0` so the distinction survives the round trip.

**3. The frontend never decides authorization.**
`useSession()` exposes the plan and role so the UI can *explain* why something is unavailable and
hide a control the user cannot use. Every one of those checks is cosmetic — the server enforces
the same rule and returns 403, 404 or an entitlement error regardless of what the client renders.
Cross-tenant reads come back as 404, so a hidden button and a forged request end in the same
place.

**4. State that matters lives on the server.**
TanStack Query caches API responses; Zustand holds only view preferences. Replay is the clearest
case: stepping a bar is `POST /replay/{id}/step`, and the response contains `visible_candles` up
to the cursor and nothing after it. The chart cannot show a future bar because the browser was
never sent one — the look-ahead guarantee is structural, not a UI convention.

Layout: `src/app/(app)/` holds the authenticated routes behind a shared shell, `src/app/` root
holds the auth pages, `src/components/ui/` the primitives, `src/components/charts/` the
lightweight-charts wrappers, and `src/lib/` the API client, query-key factory and formatters.

## Limitations

Stated plainly rather than hidden:

- **The frontend has no end-to-end test suite.** It typechecks under `strict`, lints clean and
  builds for production, and `src/lib/format.ts` — the module that keeps decimal strings away from
  IEEE doubles — is covered by unit tests. The page-level flows (import wizard, replay stepping,
  backtest submission) have been exercised only against the API by hand; Playwright specs are not
  written.
- **No live market data provider.** The bundled source is synthetic and flagged
  `is_realtime = false`; nothing is ever labelled real-time. Adding a vendor means implementing
  `MarketDataProvider` and registering it — the engine does not change.
- **User-authored strategy code is not supported.** Only registry keys can be executed. The
  interface is ready for a sandboxed worker, but that capability is deliberately absent rather
  than half-built.
- **Email invitations for people without an account are not implemented.** Adding a member
  requires an existing Tradeloom user; the endpoint says so instead of silently doing nothing.
- **Replay rebuilds state by replaying bars from the start of the session** (O(n) per step),
  which is fine for the session lengths replay is used for and is capped at 20,000 bars.
- **Multi-currency accounts are per-account, not converted.** An organization with accounts in
  different currencies gets correct per-account figures; cross-account aggregation assumes a
  single currency. FX conversion is not implemented.
