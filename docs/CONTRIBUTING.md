# Contributing

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e "backend[dev]"
pytest backend/tests -q
```

The test suite uses SQLite and needs no Postgres, Redis or MinIO.

## Before pushing

```bash
./scripts/check.sh
```

which runs: ruff, black, mypy, pytest, and the Alembic drift check.

## Conventions

**Layering.** `api → services → repositories → models`. Routes contain no business logic;
services never import from `api`; `engine` imports nothing from the application.

**Money.** `Decimal` only. Never `float` for a monetary value. Use the helpers in
`core/money.py`; `safe_div` returns `None` for undefined ratios and callers must handle it.

**Tenancy.** Tenant-owned data is reached only through a `TenantRepository`. Never write
`WHERE organization_id = ...` by hand, and never take an organization id from a request body.

**Errors.** Raise an `AppError` subclass. Keep `message` safe to show a user; put diagnostics in
`log_context`. Cross-tenant access raises `NotFoundError`, never `ForbiddenError`.

**Comments.** Explain *why*, not *what*. A comment restating the code is noise; a comment
explaining a non-obvious choice (why average cost, why 404 not 403, why this commit exists) is
the reason the next person does not undo it.

## Tests

Required for: any financial calculation, any new endpoint touching tenant data, any engine
execution rule, and any bug fix (the test should fail before the fix).

Financial tests assert **exact Decimals** computed by hand. Approximate assertions on money hide
exactly the bugs that matter.

New tenant-scoped endpoints need a case in `tests/test_tenant_isolation.py` proving user B gets
404 for user A's resource.

## Changing the engine

Bump `ENGINE_VERSION` whenever a change can alter the numbers a run produces — execution
ordering, fill pricing, cost application, metric definitions. Stored runs record the version that
produced them so results are never silently compared across versions.

New built-in strategies go in `engine/strategies/`, are added to `STRATEGY_REGISTRY`, and must
declare parameter bounds. Never add a code path that executes user-supplied text.

## Migrations

Autogenerate, then read the output. A rename appears as drop + add, which loses data — write
those by hand. Run `alembic check` before pushing.

## Definition of done

- [ ] Tests cover the behaviour, and fail without the change
- [ ] `./scripts/check.sh` passes
- [ ] Tenant isolation covered for new tenant-scoped endpoints
- [ ] Money is `Decimal`; undefined values are `None`, not `0`
- [ ] Docs updated when a rule, metric or assumption changed
- [ ] No fabricated data, no dead controls, no silently swallowed errors
