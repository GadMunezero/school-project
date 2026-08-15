#!/usr/bin/env bash
# Run the whole of Tradeloom and click through it.
#
#   scripts/demo.sh              start everything and seed a demo workspace
#   scripts/demo.sh --fresh      throw away the data and start over
#   scripts/demo.sh --down       stop everything (data is kept)
#   scripts/demo.sh --invite "Sam"   mint an invite code for a tester
#
# To let other people in, serve it from an address they can reach and close signup:
#
#   SIGNUP_MODE=invite PUBLIC_URL=https://beta.example.com scripts/demo.sh
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

# Mint an invite for a tester against an already-running stack.
if [ "${1:-}" = "--invite" ]; then
  compose ps --status running --services 2>/dev/null | grep -q '^api$' \
    || fail "the stack is not running — start it with scripts/demo.sh first"
  compose exec -T api python -m tradeloom.cli invite --note "${2:-beta tester}" --uses "${3:-1}"
  exit 0
fi

FRESH=0
[ "${1:-}" = "--fresh" ] && FRESH=1

# Where testers will reach this. Defaults to the local proxy; set PUBLIC_URL when serving through
# a tunnel or a domain:
#
#   PUBLIC_URL=https://beta.example.com scripts/demo.sh
#
# This cannot be a runtime detail: the frontend bakes its API base URL in at build time, so a
# tunnel started in front of a stack built for localhost hands every tester a page that tries to
# call *their own* machine. Setting it here means the image is rebuilt against the right origin.
PUBLIC_URL=${PUBLIC_URL:-$APP_URL}
PUBLIC_URL=${PUBLIC_URL%/}

# Cookies are marked Secure over HTTPS and must not be over plain HTTP, or the browser discards
# them and every sign-in appears to succeed and then immediately log out again.
case "$PUBLIC_URL" in
  https://*) COOKIE_SECURE=true ;;
  *)         COOKIE_SECURE=false ;;
esac

# Invites are how a closed beta stays closed. Off by default so evaluating locally stays simple.
SIGNUP_MODE=${SIGNUP_MODE:-open}

# --- configuration -----------------------------------------------------------
# Keys this script owns are updated in place; anything else in .env is left untouched, so hand
# edits survive a re-run. SECRET_KEY is generated once and never rewritten — changing it would
# invalidate every existing session cookie.
# Pure shell rather than sed -i (whose in-place flag differs between GNU and BSD) and without a
# backup file — a stray .env.bak would hold the SECRET_KEY and is not covered by .gitignore.
set_env() {
  key=$1
  value=$2
  if grep -q "^${key}=" .env 2>/dev/null; then
    tmp=$(mktemp)
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in
        "${key}="*) printf '%s=%s\n' "$key" "$value" ;;
        *)          printf '%s\n' "$line" ;;
      esac
    done < .env > "$tmp"
    cat "$tmp" > .env   # rewrite in place so the file keeps its permissions
    rm -f "$tmp"
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

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
# Written by scripts/demo.sh. Not a production configuration: see .env.example for the full set
# and docs/RUNBOOK.md for deploying properly.
TRADELOOM_ENV=development
SECRET_KEY=${SECRET}
DEBUG=false
STRIPE_ENABLED=false
ENV
  chmod 600 .env
  echo "  wrote .env with a generated SECRET_KEY"
else
  step "update .env"
fi

# Everything is served from one origin by the nginx proxy, so the browser makes same-origin
# requests and CORS never enters into it. The loopback alias is listed too, because the Origin
# header is matched as an exact string and 127.0.0.1 is not the same string as localhost.
set_env APP_PORT "$APP_PORT"
set_env NEXT_PUBLIC_API_BASE_URL "$PUBLIC_URL"
set_env BACKEND_URL "$PUBLIC_URL"
set_env FRONTEND_URL "$PUBLIC_URL"
set_env CORS_ORIGINS "${PUBLIC_URL},http://127.0.0.1:${APP_PORT}"
set_env COOKIE_SECURE "$COOKIE_SECURE"
set_env SIGNUP_MODE "$SIGNUP_MODE"
echo "  serving at ${PUBLIC_URL} (signup: ${SIGNUP_MODE})"

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

  Open        ${PUBLIC_URL}
  On this machine
              ${APP_URL}
  Sign in     demo@example.com / DemoTrader!2024

  Everything is on that one address — the app, the API and the
  interactive API docs at ${PUBLIC_URL}/docs

  Sent mail   http://localhost:8025   (nothing leaves the machine)
  File store  http://localhost:9001   (tradeloom / tradeloom-secret)

  Backtests run for real here: the Celery worker is up, so a run
  you submit is picked up and executed rather than left queued.

  Signup is '${SIGNUP_MODE}'. For a closed beta, restart with
  SIGNUP_MODE=invite and hand out codes:

      scripts/demo.sh --invite "Sam"

  scripts/demo.sh --down     stop, keeping the data
  scripts/demo.sh --fresh    wipe and start over
──────────────────────────────────────────────────────────────

BANNER
