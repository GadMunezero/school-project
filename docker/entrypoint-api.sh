#!/usr/bin/env sh
set -eu

echo "[tradeloom] waiting for database..."
python -m tradeloom.cli wait-for-db --timeout 90

echo "[tradeloom] applying migrations..."
alembic -c /app/backend/alembic.ini upgrade head

echo "[tradeloom] ensuring object storage bucket..."
python -m tradeloom.cli ensure-bucket || echo "[tradeloom] bucket check skipped (storage unavailable)"

echo "[tradeloom] starting API"
exec uvicorn tradeloom.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --proxy-headers \
  --forwarded-allow-ips '*' \
  --workers "${UVICORN_WORKERS:-2}"
