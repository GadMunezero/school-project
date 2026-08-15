#!/usr/bin/env bash
# Run the whole of Tradeloom and click through it.
#
#   scripts/demo.sh              start everything and seed a demo workspace
#   scripts/demo.sh --fresh      throw away the data and start over
#   scripts/demo.sh --down       stop everything (data is kept)
#
# Docker is the only prerequisite. This brings up the full stack — Postgres, Redis, object
# storage, a mail catcher, the API, the Celery worker and the frontend — behind a single nginx
# origin. That last part matters: the browser only ever talks to one host, so none of the
# cross-origin configuration that plagues a split localhost:3000 / localhost:8000 setup applies.
#
# The worker is what makes this different from scripts/dev.sh: backtests you submit here actually
# execute, rather than sitting queued until you run scripts/jobs.sh by hand.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$PWD

APP_PORT=${APP_PORT:-8080}
APP_URL="http://localhost:${APP_PORT}"

TRADES=${TRADES:-400}
DAYS=${DAYS:-400}

step() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }
fail() { printf '\033[31merror: %s\033[0m\n' "$1" >&2; exit 1; }

command -v docker >/dev/null || fail "Docker is required — https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required (docker compose)"
docker info >/dev/null 2>&1 || fail "Docker is installed but not running — start Docker Desktop"

compose() { APP_PORT="$APP_PORT" docker compose "$@"; }

if [ "${1:-}" = "--down" ]; then
  step "stopping"
  compose down
  echo "Stopped. Data is kept — scripts/demo.sh brings it back, --fresh wipes it."
  exit 0
fi

FRESH=0
[ "${1:-}" = "--fresh" ] && FRESH=1

# --- configuration -----------------------------------------------------------
# Written once and then left alone, so a re-run never silently changes the secret that existing
# session cookies were signed with.
if [ ! -f .env ]; then
  step "write .env"
  if command -v python3 >/dev/null 2>&1; then
    SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")
  elif command -v openssl >/dev/null 2>&1; then
    SECRET=$(openssl rand -base64 64 | tr -d '\n/+=' | head -c 64)
  else
    SECRET=$(head -c 96 /dev/urandom | base64 | tr -d '\n/+=' | head -c 64)
  fi

  cat > .env <<ENV
# Written by scripts/demo.sh for local evaluation. Not a production configuration:
# see .env.example for the full set and docs/RUNBOOK.md for deploying properly.
TRADELOOM_ENV=development
SECRET_KEY=${SECRET}
COOKIE_SECURE=false
DEBUG=false

# Everything is served from one origin by the nginx proxy, so the browser makes same-origin
# requests and CORS never enters into it. Both loopback spellings are listed anyway, because
# the Origin header is matched as an exact string.
APP_PORT=${APP_PORT}
NEXT_PUBLIC_API_BASE_URL=${APP_URL}
BACKEND_URL=${APP_URL}
FRONTEND_URL=${APP_URL}
CORS_ORIGINS=${APP_URL},http://127.0.0.1:${APP_PORT}

STRIPE_ENABLED=false
ENV
  echo "  wrote .env with a generated SECRET_KEY"
else
  echo "  reusing the existing .env"
  # The frontend bakes this in at build time, so a stale value points the browser at a port
  # nothing is listening on and every request fails as a network error.
  if ! grep -q "^NEXT_PUBLIC_API_BASE_URL=${APP_URL}$" .env 2>/dev/null; then
    printf '\033[33m  warning: .env does not set NEXT_PUBLIC_API_BASE_URL=%s\033[0m\n' "$APP_URL"
    printf '\033[33m           the app will load but sign-in will fail to reach the API.\033[0m\n'
  fi
fi

if [ "$FRESH" = 1 ]; then
  step "remove existing data"
  compose down -v
fi

# --- build and start ---------------------------------------------------------
step "build and start (first run pulls images and builds — several minutes)"
compose up -d --build

step "wait for the stack to be ready"
READY=0
for _ in $(seq 1 180); do
  if curl -fsS -o /dev/null "${APP_URL}/api/v1/health/ready" 2>/dev/null; then READY=1; break; fi
  sleep 2
done
[ "$READY" = 1 ] || {
  printf '\033[31mthe API never became ready. Recent logs:\033[0m\n' >&2
  compose logs --tail 40 api >&2
  exit 1
}

# --- demo data ---------------------------------------------------------------
# Seeding twice would stack a second workspace on top of the first, so ask the database whether
# this has already been done rather than tracking it in a file that can drift.
USERS=$(compose exec -T api python -c "
import asyncio
from sqlalchemy import func, select
from tradeloom.db import session_scope
from tradeloom.models.identity import User

async def main() -> None:
    async with session_scope() as session:
        print((await session.execute(select(func.count()).select_from(User))).scalar_one())

asyncio.run(main())
" 2>/dev/null | tr -d '\r\n ') || USERS=""

if [ "$USERS" = "0" ]; then
  step "seed the demo workspace (${TRADES} trades, ${DAYS} days of candles)"
  compose exec -T api python -m tradeloom.cli seed --demo --trades "$TRADES" --days "$DAYS"
elif [ -z "$USERS" ]; then
  fail "could not read the database to decide whether to seed — see: docker compose logs api"
else
  echo "  ${USERS} account(s) already present, leaving the data alone (--fresh to start over)"
fi

cat <<BANNER

──────────────────────────────────────────────────────────────
  Tradeloom is running.

  Open        ${APP_URL}
  Sign in     demo@example.com / DemoTrader!2024

  Everything is on that one address — the app, the API and the
  interactive API docs at ${APP_URL}/docs

  Sent mail   http://localhost:8025   (nothing leaves the machine)
  File store  http://localhost:9001   (tradeloom / tradeloom-secret)

  Backtests run for real here: the Celery worker is up, so a run
  you submit is picked up and executed rather than left queued.

  scripts/demo.sh --down     stop, keeping the data
  scripts/demo.sh --fresh    wipe and start over
──────────────────────────────────────────────────────────────

BANNER
