#!/usr/bin/env bash
# Restore a dump into a scratch database and check that what came back is usable.
#
#   scripts/restore-check.sh backups/tradeloom-20260815T030000Z.dump
#
# An untested backup is a hypothesis. This turns it into a fact, and it is safe to run against a
# production dump on any machine with Postgres: it only ever creates and drops a scratch database
# whose name it chooses itself, and it never writes to the database the dump came from.
#
# Run it after every schema change, and on a schedule — a backup that stopped working three weeks
# ago looks exactly like one that works, right up until you need it.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$PWD

DUMP=${1:-}
if [ -z "$DUMP" ] || [ ! -f "$DUMP" ]; then
  echo "usage: scripts/restore-check.sh <dump-file>" >&2
  exit 2
fi

# Where to build the scratch database. Defaults to the local superuser socket.
ADMIN_URL=${RESTORE_ADMIN_URL:-postgresql://localhost/postgres}
SCRATCH_DB="tradeloom_restore_check_$$"
SCRATCH_URL="${ADMIN_URL%/*}/$SCRATCH_DB"

PY=${PYTHON:-.venv/bin/python}
[ -x "$PY" ] || PY=python3

step() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

cleanup() {
  psql "$ADMIN_URL" -q -c "DROP DATABASE IF EXISTS \"$SCRATCH_DB\";" >/dev/null 2>&1 || true
}
trap cleanup EXIT

step "create a scratch database"
psql "$ADMIN_URL" -q -c "CREATE DATABASE \"$SCRATCH_DB\";" || fail "could not create the scratch database"

step "restore $(basename "$DUMP") into it"
# --no-owner because the dump's roles need not exist here; errors are fatal so a partial restore
# cannot be mistaken for a good one.
pg_restore --no-owner --no-privileges --exit-on-error --dbname="$SCRATCH_URL" "$DUMP" \
  || fail "the dump did not restore cleanly"

step "check the schema matches the application's models"
DATABASE_URL="postgresql+asyncpg://${SCRATCH_URL#postgresql://}" \
SECRET_KEY=restore-check-only-not-a-real-secret-key-0123456789 \
PYTHONPATH="$ROOT:$ROOT/backend" \
  "${PY%/*}/alembic" -c backend/alembic.ini check \
  || fail "the restored schema does not match the models — the dump predates a migration"

step "count what came back"
# Counted directly rather than read from pg_stat_user_tables, whose estimates are zero until an
# ANALYZE runs — which would make an empty restore look identical to a healthy one.
TOTAL=0
for table in users organizations accounts trades market_data backtest_runs; do
  COUNT=$(psql "$SCRATCH_URL" -tAc "SELECT count(*) FROM \"$table\";" 2>/dev/null || echo "?")
  printf '  %-16s %s\n' "$table" "$COUNT"
  case "$COUNT" in
    ''|*[!0-9]*) fail "could not count $table — the restore is incomplete" ;;
    *) TOTAL=$((TOTAL + COUNT)) ;;
  esac
done

USERS=$(psql "$SCRATCH_URL" -tAc "SELECT count(*) FROM users;")
[ "$USERS" -gt 0 ] || fail "no users in the restored database — this dump would not bring anyone back"
[ "$TOTAL" -gt 0 ] || fail "the restored database is empty"

printf '\n\033[32m✓ restore verified: schema current, %s user(s) and %s rows across core tables\033[0m\n' "$USERS" "$TOTAL"
