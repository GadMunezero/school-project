#!/usr/bin/env bash
# Bring up a real stack (seeded database, API, production frontend) and run the E2E suite.
#
# Nothing here is mocked. The frontend is served the same way the container image serves it —
# `node .next/standalone/server.js` — so a build-output problem fails here rather than in
# production.
#
# Usage: scripts/e2e.sh [playwright args...]
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$PWD

PY=${PYTHON:-.venv/bin/python}
if [ ! -x "$PY" ]; then PY=python3; fi

# `localhost` everywhere, deliberately: the CORS allow-list and the session cookie's host are
# matched as strings, so mixing in `127.0.0.1` makes the browser treat them as separate origins.
API_PORT=${E2E_API_PORT:-8000}
WEB_PORT=${E2E_WEB_PORT:-3000}
API_URL="http://localhost:${API_PORT}"
WEB_URL="http://localhost:${WEB_PORT}"

WORK=$(mktemp -d)
export DATABASE_URL="sqlite+aiosqlite:///${WORK}/e2e.db"
export SECRET_KEY=e2e-only-secret-key-not-used-anywhere-else-0123456789
# `test` swaps exactly four things for their local equivalents — object storage goes in-memory
# instead of S3, email is not sent, rate limiting is off, and the health probe reports detail.
# Everything the suite actually asserts on (auth, tenancy, the position builder, the engine) runs
# the same code as production.
export TRADELOOM_ENV=test
export RATE_LIMIT_ENABLED=false
export EMAIL_ENABLED=false
export STRIPE_ENABLED=false
# Argon2 at production cost would dominate the run; the algorithm is unchanged.
export ARGON2_TIME_COST=1 ARGON2_MEMORY_COST_KIB=8192 ARGON2_PARALLELISM=1
export CORS_ORIGINS="$WEB_URL"
export BACKEND_URL="$API_URL" FRONTEND_URL="$WEB_URL"
export PYTHONPATH="$ROOT:$ROOT/backend"

API_PID="" WEB_PID=""
cleanup() {
  [ -n "$WEB_PID" ] && kill "$WEB_PID" 2>/dev/null || true
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT

step() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }

wait_for() { # url, label
  for _ in $(seq 1 60); do
    if curl -sS -o /dev/null "$1" 2>/dev/null; then return 0; fi
    sleep 1
  done
  echo "timed out waiting for $2 at $1" >&2
  return 1
}

step "seed a throwaway database"
"$PY" -m tradeloom.cli reset --force >/dev/null
"$PY" -m tradeloom.cli seed --demo --trades 150 --days 120

step "start the API on :${API_PORT}"
"$PY" -m uvicorn tradeloom.main:app --host 127.0.0.1 --port "$API_PORT" >"$WORK/api.log" 2>&1 &
API_PID=$!
wait_for "$API_URL/api/v1/auth/session" "the API" || { cat "$WORK/api.log"; exit 1; }

step "build the frontend against ${API_URL}"
# NEXT_PUBLIC_* is inlined at build time, so the API URL must be set here, not at start time.
(cd frontend && NEXT_PUBLIC_API_BASE_URL="$API_URL" npm run build >"$WORK/build.log" 2>&1) \
  || { tail -40 "$WORK/build.log"; exit 1; }

# `output: standalone` emits a self-contained server but does not copy these in.
cp -r frontend/.next/static frontend/.next/standalone/.next/static
[ -d frontend/public ] && cp -r frontend/public frontend/.next/standalone/public

step "serve the production build on :${WEB_PORT}"
(cd frontend/.next/standalone && PORT="$WEB_PORT" HOSTNAME=127.0.0.1 node server.js) \
  >"$WORK/web.log" 2>&1 &
WEB_PID=$!
wait_for "$WEB_URL/login" "the frontend" || { cat "$WORK/web.log"; exit 1; }

step "run the end-to-end suite"
cd tests
[ -d node_modules ] || npm install --no-audit --no-fund >/dev/null
E2E_WEB_URL="$WEB_URL" E2E_API_URL="$API_URL" npx playwright test "$@"
