# Database

PostgreSQL 16 in production. The test suite runs on SQLite through portable column types, so
`pytest` needs no external services while keeping identical semantics (UUID identity, aware
datetimes, exact decimals).

46 tables. Schema is owned by Alembic; `alembic check` runs in CI and fails on drift.

## Conventions

| Convention | Choice | Why |
| --- | --- | --- |
| Primary keys | UUIDv4, generated in Python | lets services build object graphs before a flush, and removes a round trip per insert |
| Timestamps | `TIMESTAMPTZ`, always UTC | localisation is a presentation concern |
| Money / price / quantity | `NUMERIC(28,10)` | exact; never float |
| Percent / ratio | `NUMERIC(18,8)` | |
| Enums | `VARCHAR(n)` + validated Python enum | adding a value needs no `ALTER TYPE` and no lock |
| JSON | `JSONB` on PG, `JSON` elsewhere | indexable where it matters |
| Soft delete | `deleted_at` where recovery matters | trades, accounts, strategies, tags |
| Naming | explicit convention in `db/base.py` | stable, reversible Alembic output |

Every timestamp column rejects naive datetimes at the type level.

## Tenancy

Every tenant-owned table carries `organization_id` with `ON DELETE CASCADE`, indexed and used by
`TenantRepository` on every query. Deleting a workspace removes its data by cascade rather than by
application code that could miss a table.

`instruments` and `instrument_aliases` allow `organization_id IS NULL`, which means the shared
catalogue visible to every tenant.

Market data (`market_data_sources`, `market_data`, `market_data_coverage`) is intentionally
**not** tenant-scoped — candles are reference data. Access still goes through a tenant-scoped
instrument lookup, so one workspace cannot enumerate another's private instruments.

## Table groups

**Identity** — `users`, `sessions`, `oauth_accounts`, `email_tokens`, `login_attempts`

Sessions store only a token digest. `email_tokens` covers verification and reset, single-use.
`login_attempts` backs lockout and gives admins an auth-failure view.

**Workspaces** — `organizations`, `organization_members`, `roles`, `permissions`,
`role_permissions`

**Trading** — `accounts`, `cash_transactions`, `account_snapshots`, `instruments`,
`instrument_aliases`, `orders`, `positions`, `trades`, `trade_tags`

`orders` are the atomic executions; `trades` are derived round trips; `positions` is a live
cache. `cash_transactions` is an append-only ledger — balances are recomputed from it, never
incremented in place.

**Classification** — `strategies`, `strategy_versions`, `strategy_parameters`, `setups`, `tags`

A backtest references an immutable `strategy_versions` row, so a result stays reproducible after
the parent strategy is edited.

**Journal & files** — `journal_entries`, `screenshots`, `file_objects`

PostgreSQL stores only upload *metadata*; bytes live in S3.

**Imports** — `imports`, `import_rows`, `import_templates`

Every uploaded row is retained with its status and field-level errors. `import_id` on the created
orders and trades is what makes revert exact.

**Backtesting** — `backtests`, `backtest_runs`, `backtest_trades`, `backtest_orders`,
`equity_points`, `drawdown_points`, `replay_sessions`

Simulated results live in their own tables so they can never be mistaken for, or aggregated into,
a real journal.

**Platform** — `analytics_snapshots`, `subscriptions`, `subscription_events`, `notifications`,
`job_records`, `audit_logs`

`subscription_events.external_event_id` is unique — that constraint *is* the webhook idempotency
mechanism. `audit_logs` is append-only.

## Notable constraints

```sql
-- At most one open position per account+instrument (partial unique index).
CREATE UNIQUE INDEX uq_positions_open_account_instrument
  ON positions (account_id, instrument_id) WHERE status = 'open';

-- Duplicate detection for imports.
UNIQUE (account_id, external_id)  -- on orders and on trades

-- One candle per series bar.
UNIQUE (source_id, instrument_id, timeframe, opened_at)  -- on market_data

-- Data sanity.
CHECK (high >= low)              -- market_data
CHECK (quantity > 0)             -- trades, orders
CHECK (remaining_quantity >= 0)  -- trades
CHECK (leverage > 0)             -- accounts
```

### One deliberate `use_alter`

`users.avatar_file_id → file_objects → organizations → users` is a foreign-key cycle.
PostgreSQL cannot order the `CREATE TABLE` statements, so that one constraint is emitted as a
deferred `ALTER TABLE`.

## Indexing

Composite indexes lead with `organization_id` because every query filters on it. Partial indexes
cover the hot paths — open trades, unread notifications, active jobs, non-deleted trades — so
they stay small.

## Migrations

```bash
alembic -c backend/alembic.ini upgrade head
alembic -c backend/alembic.ini revision --autogenerate -m "description"
alembic -c backend/alembic.ini check          # CI: fails on model/migration drift
alembic -c backend/alembic.ini downgrade -1
```

The URL always comes from `DATABASE_URL`, never from `alembic.ini` — one source of truth means a
migration cannot be applied to the wrong database because two files disagreed.

Review autogenerated migrations before committing: Alembic detects added and dropped columns, but
renames appear as drop + add, which loses data.
