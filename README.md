# Tradeloom

**Weave your trades into insight.**

Tradeloom is a multi-tenant SaaS platform for discretionary and systematic traders. It combines
four products into one workspace:

1. **Trading journal** — import or record every fill, reconstruct positions, annotate trades.
2. **Portfolio analytics** — a filterable analytics engine computed server-side from real trades.
3. **Strategy management** — versioned strategies, parameter schemas, setups and tags.
4. **Backtesting & replay** — a deterministic, event-driven simulation engine plus a candle-by-candle
   replay mode that uses the *same* execution rules as the backtester.

> This repository is original work. It is functionally inspired by the general category of trading
> journals, but shares no source code, assets, copy, or visual design with any commercial product.

---

## Repository layout

```
backend/     FastAPI application, domain services, and the backtesting engine package
worker/      Celery worker (imports the backend package; separate deployable)
frontend/    Next.js 15 App Router client
data/        Seed fixtures and sample broker CSV files
docker/      Dockerfiles and nginx configuration
scripts/     Developer and CI helper scripts
docs/        Architecture, database, backtesting, API, security and deployment docs
tests/       End-to-end (Playwright) suite spanning the whole stack
```

Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) first — it explains the module boundaries
and why business logic lives in services rather than routes or React components.

---

## Quick start (one command)

```bash
scripts/dev.sh
```

Creates a Python virtualenv, installs dependencies, builds and seeds a local SQLite database, and
starts the API on `http://localhost:8000` and the app on `http://localhost:3000`. No Postgres, no
Redis, no Docker. Sign in with `demo@example.com` / `DemoTrader!2024`.

Pass `--fresh` to wipe the local database and re-seed.

The demo workspace includes a backtest that has already been run, so the results page has real
engine output the moment you open it. Submitting a *new* backtest queues it for a Celery worker,
which needs Redis — rather than make you stand that up, drain the queue from another shell:

```bash
scripts/jobs.sh            # execute whatever is queued
scripts/jobs.sh --watch    # keep executing as runs arrive
```

That calls the same `BacktestService.execute` the worker calls, so a run that succeeds there
succeeds here, and one that fails, fails identically.

---

## Quick start (Docker — the whole system)

```bash
scripts/demo.sh
```

Docker is the only prerequisite. This is the closest thing to running the real product: Postgres,
Redis, MinIO (S3-compatible), a mail catcher, the API, the **Celery worker**, the Next.js frontend
and an nginx proxy. It writes a `.env` with a generated `SECRET_KEY`, waits for the stack to come
up, seeds a demo workspace if the database is empty, and prints where to go.

Everything is served from **one origin**, `http://localhost:8080` — nginx routes `/api/` to the API
and everything else to the frontend. That is worth knowing: because the browser only ever talks to
a single host, the cross-origin configuration that a split `localhost:3000` / `localhost:8000`
setup needs does not apply at all.

| | |
| --- | --- |
| App and API | `http://localhost:8080` (API docs at `/docs`) |
| Sign in | `demo@example.com` / `DemoTrader!2024` |
| Sent mail | `http://localhost:8025` — nothing leaves the machine |
| File storage | `http://localhost:9001` |

```bash
scripts/demo.sh --down     # stop, keeping the data
scripts/demo.sh --fresh    # wipe and start over
```

The worker is the reason to prefer this over `scripts/dev.sh`: a backtest you submit here is picked
up and executed, instead of sitting queued until you drain it by hand.

---

## Quick start (local, no Docker)

You need Python 3.11+ (3.12 recommended), Node 20+, Postgres 15+ and Redis 7+.

```bash
# Backend
python -m venv .venv && source .venv/bin/activate
pip install -e "backend[dev]"
export DATABASE_URL=postgresql+asyncpg://tradeloom:tradeloom@localhost:5432/tradeloom
alembic -c backend/alembic.ini upgrade head
python -m tradeloom.cli seed --demo
uvicorn tradeloom.main:app --reload --port 8000

# Worker (second shell)
celery -A worker.celery_app.celery_app worker -l info

# Frontend (third shell)
cd frontend && npm install && npm run dev
```

The backend runs against SQLite automatically when `DATABASE_URL` points at `sqlite+aiosqlite://`,
which is what the test suite uses — no Postgres required to run `pytest`.

---

## Running a closed beta

Registration is open by default, which is what the code has always done. Set `SIGNUP_MODE=invite`
and the only way in is a code an administrator issued — `.env.example` ships that way, so a fresh
install starts closed.

```bash
# Make yourself staff, once.
python -m tradeloom.cli create-admin --email you@example.com

# Mint a code, either from the CLI…
python -m tradeloom.cli invite --note "Jamie, from the futures forum"

# …or under Administration → Invites, which also shows who redeemed what.
```

An invite is single-use and expires in 30 days unless you say otherwise; raise `--uses` for a
cohort. Revoking one stops it working immediately. A code is a ticket, not a credential: it grants
nothing beyond the right to register, which is why the console shows it in full so you can send it
to someone.

Every refusal reads the same — unknown, spent, expired and revoked codes are indistinguishable to
whoever is typing, so the form cannot be used to discover which codes exist.

---

## Before real users: the legal documents

`content/legal/terms.md` and `content/legal/privacy.md` ship as **placeholders that say so**.
Nobody should write your terms of service for you, least of all a language model, so the
repository does not pretend to have done it.

Both pages, the consent checkbox and the acceptance record all work end to end against them —
what is missing is only the text. Write each document, delete the `UNWRITTEN-PLACEHOLDER` marker
on its first line, and bump the matching entry in `VERSIONS` in
`backend/tradeloom/core/legal.py`.

Until you do, `validate_for_production()` refuses to boot a production process, and the pages
carry a visible warning. Both are deliberate: recording that users accepted repository
boilerplate would be worse than having no terms at all.

Consent is recorded per user, per document, per version, with the time and the request's IP and
user agent — so "which version did this person agree to" has an answer after the text changes.

---

## Verification commands

| What | Command |
| --- | --- |
| Backend lint | `ruff check backend worker && black --check backend worker` |
| Backend types | `mypy backend/tradeloom` |
| Backend tests | `pytest backend/tests -q` |
| Frontend lint | `cd frontend && npm run lint` |
| Frontend types | `cd frontend && npm run typecheck` |
| Frontend unit tests | `cd frontend && npm run test` |
| Frontend build | `cd frontend && npm run build` |
| End-to-end | `./scripts/e2e.sh` |
| Everything | `./scripts/check.sh` |

`scripts/e2e.sh` seeds a throwaway SQLite database, starts the API, builds the client and serves
the standalone output — the same artefact the container image runs — then drives it with
Playwright. Nothing is stubbed, so a change to the position builder shows up as a changed number
on a page.

CI (`.github/workflows/ci.yml`) runs the same commands plus a migration-drift check.

---

## Documentation

| Document | Contents |
| --- | --- |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layering rules, request lifecycle, tenancy model |
| [DATABASE.md](docs/DATABASE.md) | Every table, key constraints, and the numeric conventions |
| [BACKTESTING.md](docs/BACKTESTING.md) | Event flow, execution models, look-ahead guarantees |
| [FINANCIALS.md](docs/FINANCIALS.md) | Exact definitions of every P&L and performance metric |
| [API.md](docs/API.md) | Envelope format, pagination, filtering, error codes |
| [SECURITY.md](docs/SECURITY.md) | Auth, session rotation, tenant isolation, upload rules |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Production topology, backups, monitoring, retention |
| [RUNBOOK.md](docs/RUNBOOK.md) | One server, start to finish: domain, TLS, first invite, verified restore |
| [CONTRIBUTING.md](docs/CONTRIBUTING.md) | Conventions and the definition of done |

## Status and known limitations

Tradeloom is honest about what is real. See the "Limitations" section of
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — nothing in the UI fabricates numbers, and every
feature that is not fully implemented is isolated behind an interface and documented there rather
than faked.
