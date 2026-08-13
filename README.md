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
shared/      Cross-language contracts (enums shared by Python and TypeScript)
data/        Seed fixtures and sample broker CSV files
docker/      Dockerfiles and nginx configuration
scripts/     Developer and CI helper scripts
docs/        Architecture, database, backtesting, API, security and deployment docs
tests/       End-to-end (Playwright) suite spanning the whole stack
```

Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) first — it explains the module boundaries
and why business logic lives in services rather than routes or React components.

---

## Quick start (Docker)

```bash
cp .env.example .env
docker compose up --build
```

This starts Postgres, Redis, MinIO (S3-compatible), the API, the Celery worker, the Next.js
frontend, and an nginx reverse proxy. The API waits for Postgres to become healthy, runs Alembic
migrations, and then serves on `http://localhost:8000`. The frontend is on `http://localhost:3000`
and nginx fronts both on `http://localhost:8080`.

Load the demo workspace (a demo user, three accounts, thousands of generated trades, candles,
strategies and a completed backtest):

```bash
docker compose exec api python -m tradeloom.cli seed --demo
```

Then sign in with the credentials the seeder prints (default `demo@example.com` /
`DemoTrader!2024`).

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
| [CONTRIBUTING.md](docs/CONTRIBUTING.md) | Conventions and the definition of done |

## Status and known limitations

Tradeloom is honest about what is real. See the "Limitations" section of
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — nothing in the UI fabricates numbers, and every
feature that is not fully implemented is isolated behind an interface and documented there rather
than faked.
