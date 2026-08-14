#!/usr/bin/env bash
# Execute queued backtest runs against the local development database.
#
# Submitting a backtest queues it for a Celery worker, which needs Redis. This runs the queue in
# the foreground instead, through the same `BacktestService.execute` the worker calls — so a run
# that succeeds here would have succeeded there, and one that fails, fails there too.
#
#   scripts/jobs.sh            drain whatever is queued
#   scripts/jobs.sh --watch    keep draining every few seconds
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$PWD

[ -x .venv/bin/python ] || { echo "run scripts/dev.sh first" >&2; exit 1; }

export DATABASE_URL="sqlite+aiosqlite:///${ROOT}/.tradeloom-dev.db"
export SECRET_KEY="local-development-only-secret-key-0123456789"
export TRADELOOM_ENV=development
export PYTHONPATH="$ROOT:$ROOT/backend"

if [ "${1:-}" = "--watch" ]; then
  echo "watching for queued backtests; Ctrl-C to stop"
  while true; do
    .venv/bin/python -m tradeloom.cli run-jobs | grep -v "^no queued runs$" || true
    sleep 3
  done
fi

.venv/bin/python -m tradeloom.cli run-jobs
