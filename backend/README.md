# Tradeloom backend

FastAPI application, domain services, and the `tradeloom.engine` backtesting package.

```
tradeloom/
  api/          HTTP layer only: routing, dependency wiring, serialization
  core/         cross-cutting primitives (settings, logging, security, money, errors)
  db/           engine/session management, portable column types, base model
  models/       SQLAlchemy ORM models
  schemas/      Pydantic request/response contracts
  repositories/ tenant-scoped data access
  services/     business logic (the only place financial rules live)
  engine/       standalone deterministic backtesting engine (no DB imports)
  seed/         demo data generation
  cli.py        operational commands (wait-for-db, seed, reset, ensure-bucket)
```

Layering rule: `api → services → repositories → models`. Services never import from `api`,
and `engine` imports nothing from the rest of the application.

Run the tests with `pytest backend/tests -q` — they use SQLite and need no external services.
