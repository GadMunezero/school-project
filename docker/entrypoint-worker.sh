#!/usr/bin/env sh
set -eu

echo "[tradeloom] worker waiting for database..."
python -m tradeloom.cli wait-for-db --timeout 90

exec celery -A worker.celery_app.celery_app worker \
  --loglevel "${CELERY_LOG_LEVEL:-info}" \
  --concurrency "${CELERY_CONCURRENCY:-2}" \
  -Q "${CELERY_QUEUES:-default,backtests,imports,analytics,maintenance}"
