#!/usr/bin/env bash
# Everything CI runs, in the same order. Run before pushing.
set -euo pipefail

cd "$(dirname "$0")/.."
PY=${PYTHON:-.venv/bin/python}
if [ ! -x "$PY" ]; then PY=python3; fi
BIN=$(dirname "$PY")

step() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }

step "ruff"
"$BIN/ruff" check backend worker

step "black"
"$BIN/black" --check -l 100 backend worker

step "mypy"
"$BIN/mypy" backend/tradeloom || echo "  (type errors reported; not yet blocking — see docs/CONTRIBUTING.md)"

step "pytest"
PYTHONPATH="$PWD:$PWD/backend" "$BIN/python" -m pytest backend/tests -q

step "migration drift"
SCRATCH=$(mktemp -d)
DATABASE_URL="sqlite+aiosqlite:///$SCRATCH/check.db" SECRET_KEY=ci-only-secret-key \
  "$BIN/alembic" -c backend/alembic.ini upgrade head >/dev/null
DATABASE_URL="sqlite+aiosqlite:///$SCRATCH/check.db" SECRET_KEY=ci-only-secret-key \
  "$BIN/alembic" -c backend/alembic.ini check
rm -rf "$SCRATCH"

# The frontend checks need node_modules; skip rather than fail if the client hasn't been installed.
if [ -d frontend/node_modules ]; then
  step "frontend lint"
  (cd frontend && npm run --silent lint)

  step "frontend types"
  (cd frontend && npm run --silent typecheck)

  step "frontend tests"
  (cd frontend && npm run --silent test)

  step "frontend build"
  (cd frontend && npm run --silent build)
else
  printf '\n\033[33m▸ frontend checks skipped (run "cd frontend && npm install" first)\033[0m\n'
fi

printf '\n\033[32mAll checks passed.\033[0m\n'
