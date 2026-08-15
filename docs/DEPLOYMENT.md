# Deployment

## Topology

| Component | Notes |
| --- | --- |
| API | stateless FastAPI; scale horizontally behind a load balancer |
| Worker | Celery; scale per queue (`backtests` is the CPU-hungry one) |
| Beat | scheduler — run **exactly one** instance |
| PostgreSQL | managed instance recommended; the system of record |
| Redis | broker, result backend, rate limiting |
| Object storage | S3, MinIO, R2 or Spaces |

Everything except Postgres and object storage is disposable.

## Before first boot

`Settings.validate_for_production()` refuses to start a production process with development
defaults. It blocks on:

- `SECRET_KEY` still a placeholder or shorter than 48 characters
- `COOKIE_SECURE` false
- `DEBUG` true
- `DATABASE_URL` pointing at SQLite
- `STRIPE_ENABLED` without `STRIPE_WEBHOOK_SECRET`
- missing S3 credentials

That is a feature: a misconfigured deployment fails loudly at boot rather than quietly serving
insecure cookies.

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"   # SECRET_KEY
```

## Migrations

```bash
alembic -c backend/alembic.ini upgrade head
```

Run **once per release, before** rolling out new API instances. The container entrypoint does
this automatically; with several replicas, prefer a dedicated migration job so two containers do
not race.

Migrations must be backward compatible for the duration of a rolling deploy. For a destructive
change, use expand/contract: add the new column, deploy code writing both, backfill, deploy code
reading the new one, drop the old column in a later release.

## Reverse proxy

`docker/nginx.conf` is a working starting point. Requirements:

- terminate TLS and forward `X-Forwarded-Proto` (the app trusts it for cookie handling)
- forward `X-Forwarded-For` (rate limiting and audit logs use it)
- `client_max_body_size` at least `UPLOAD_MAX_BYTES`
- `proxy_read_timeout` ≥ 120s

## Stripe webhooks

1. Point an endpoint at `https://your-domain/api/v1/billing/webhook`.
2. Subscribe to `checkout.session.completed`, `customer.subscription.*`, `invoice.payment_*`.
3. Put the signing secret in `STRIPE_WEBHOOK_SECRET`.

The route must receive the **raw body** — any proxy that re-serialises JSON breaks signature
verification. Redelivery is safe: the unique constraint on `external_event_id` makes processing
idempotent.

## Health checks

| Probe | Endpoint | Behaviour |
| --- | --- | --- |
| Liveness | `/health/live` | never touches a dependency, so a database blip does not kill healthy pods |
| Readiness | `/health/ready` | 503 when Postgres is unreachable; Redis down reports `degraded` |

## Backups

The database is the only irreplaceable component. Object storage holds user uploads and should be
backed up or versioned too.

```bash
# Nightly, from cron. Writes a compressed custom-format dump and prunes old ones.
BACKUP_DIR=/srv/backups scripts/backup.sh

# Weekly. Restores into a scratch database, checks the schema against the models, counts the
# core tables, and drops the scratch database again. Never touches the source database.
scripts/restore-check.sh /srv/backups/tradeloom-20260815T030000Z.dump

# A real restore, when you actually need one:
pg_restore --clean --if-exists --no-owner --dbname="$TARGET_URL" /srv/backups/<dump>
```

Recommended: nightly logical dumps retained 30 days, plus continuous WAL archiving (PITR) for a
recovery point measured in minutes. A managed provider's automated backups usually cover both.

**Restore drills matter more than backups.** `scripts/restore-check.sh` is that drill, and CI runs
it on every push so the scripts themselves cannot rot. An untested backup is a hypothesis — this
one found a foreign key the models declared and the initial migration never emitted, missing from
every PostgreSQL database the schema had ever created and invisible to the SQLite drift check.

Encrypt backups at rest — a dump contains every user's complete trading history.

## Monitoring

Logs are structured JSON with a `request_id` on every line; ship them somewhere queryable.

Worth alerting on:

| Signal | Condition |
| --- | --- |
| API 5xx rate | > 1% over 5 minutes |
| `/health/ready` | failing on any instance |
| `job_records` failures | failed jobs rising, or queue depth growing without completions |
| Auth failures | spike in `login_failed` (credential stuffing) |
| Database | connection saturation, replication lag, disk headroom |
| Celery | no heartbeat from a queue's workers |

`GET /api/v1/admin/overview` gives an at-a-glance view of users, jobs, failed imports and recent
auth failures.

## Scheduled jobs

Celery beat runs: account snapshot refresh (hourly), expired-file cleanup (daily), expired
session/token purge (daily), and account-deletion processing (daily).

Deletion requests execute after a 7-day grace period. Signing in during it cancels the request.

## Data retention

| Data | Policy |
| --- | --- |
| Trades, accounts | retained until the user deletes them or their account |
| Sessions, email tokens | purged 30 days after expiry |
| Login attempts | purged after 90 days (the audit log keeps the security trail) |
| Generated exports | deleted after `expires_at` (default 7 days) |
| Audit logs | retained; append-only |
| Deleted accounts | workspaces cascade-deleted; the user row is anonymised so the audit log keeps no holes |

## Scaling notes

- The API is stateless; sessions live in Postgres, so any instance can serve any request.
- Give `backtests` its own worker pool — a long run must not delay a 2-second import.
- `worker_prefetch_multiplier=1` with `task_acks_late` means a worker never hoards jobs it cannot
  start, and a crashed run is redelivered rather than lost.
- Analytics caps at 50,000 trades per query and says so via a `truncated` flag rather than
  silently returning partial results.
- Equity curves are downsampled to 5,000 points on write, so a multi-year 1-minute run does not
  write millions of rows.
