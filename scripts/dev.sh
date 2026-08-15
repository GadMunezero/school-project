#!/usr/bin/env bash
# Run Tradeloom locally with one command.
#
# Uses SQLite and no external services, so there is nothing to install beyond Python and Node. The
# database is created and seeded on first run and reused afterwards; pass --fresh to start over.
#
#   scripts/dev.sh              start the API and the frontend
#   scripts/dev.sh --fresh      wipe the local database and re-seed first
#
# Backtests are queued for a Celery worker, which needs Redis. Rather than make you stand that up
# to try the feature, run this in a second shell whenever you submit one:
#
#   scripts/jobs.sh
#
# It executes queued runs through the same code the worker uses.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$PWD

FRESH=0
[ "${1:-}" = "--fresh" ] && FRESH=1

API_PORT=${API_PORT:-8000}
WEB_PORT=${WEB_PORT:-3000}
API_URL="http://localhost:${API_PORT}"
WEB_URL="http://localhost:${WEB_PORT}"

step() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }
fail() { printf '\033[31merror: %s\033[0m\n' "$1" >&2; exit 1; }

command -v node >/dev/null || fail "Node 20+ is required (https://nodejs.org)"

# --- python ------------------------------------------------------------------
if [ ! -x .venv/bin/python ]; then
  step "create the Python environment"
  command -v python3 >/dev/null || fail "Python 3.11+ is required"
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -e "backend[dev]"
fi
PY=.venv/bin/python

# --- configuration -----------------------------------------------------------
# Everything the app needs to run on a laptop, with no Postgres, Redis or S3.
export DATABASE_URL="sqlite+aiosqlite:///${ROOT}/.tradeloom-dev.db"
export SECRET_KEY="local-development-only-secret-key-0123456789"
export TRADELOOM_ENV=development
export EMAIL_ENABLED=false
export STRIPE_ENABLED=false
export RATE_LIMIT_ENABLED=false
# Both spellings of the loopback host. CORS matches the Origin header as an exact string, so a
# browser pointed at 127.0.0.1 while this listed only localhost got no allow-origin header back,
# the fetch rejected, and the sign-in form reported "could not reach the server" — which reads as
# a wrong password rather than the configuration problem it is.
export CORS_ORIGINS="${WEB_URL},http://127.0.0.1:${WEB_PORT}"
export BACKEND_URL="$API_URL" FRONTEND_URL="$WEB_URL"
export PYTHONPATH="$ROOT:$ROOT/backend"

if [ "$FRESH" = 1 ]; then
  step "wipe the local database"
  rm -f "${ROOT}/.tradeloom-dev.db"
fi

# --- database ----------------------------------------------------------------
if [ ! -f "${ROOT}/.tradeloom-dev.db" ]; then
  step "create and seed the database"
  "$PY" -m tradeloom.cli reset --force >/dev/null
  "$PY" -m tradeloom.cli seed --demo --trades 400 --days 400
else
  step "reusing the existing database (--fresh to start over)"
fi

# --- frontend dependencies ---------------------------------------------------
if [ ! -d frontend/node_modules ]; then
  step "install frontend dependencies (this takes a minute the first time)"
  (cd frontend && npm install --no-audit --no-fund)
fi

# --- servers -----------------------------------------------------------------
API_PID="" WEB_PID=""
cleanup() {
  [ -n "$WEB_PID" ] && kill "$WEB_PID" 2>/dev/null || true
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

step "start the API on ${API_URL}"
"$PY" -m uvicorn tradeloom.main:app --host 127.0.0.1 --port "$API_PORT" &
API_PID=$!

for _ in $(seq 1 60); do
  curl -sS -o /dev/null "${API_URL}/api/v1/auth/session" 2>/dev/null && break
  sleep 1
done

step "start the frontend on ${WEB_URL}"
(cd frontend && NEXT_PUBLIC_API_BASE_URL="$API_URL" PORT="$WEB_PORT" npm run dev) &
WEB_PID=$!

cat <<BANNER

──────────────────────────────────────────────────────────────
  Tradeloom is running.

  Open        ${WEB_URL}
  Sign in     demo@example.com / DemoTrader!2024

  The demo workspace has trades, candles, strategies and one
  backtest that has already been run — open Backtester to see
  its results straight away.

  Submitting a new backtest queues it. To execute the queue,
  run this in another shell:

      scripts/jobs.sh

  Press Ctrl-C to stop.
──────────────────────────────────────────────────────────────

BANNER

wait
