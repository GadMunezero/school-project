#!/usr/bin/env bash
# Take a compressed logical backup of the database.
#
#   scripts/backup.sh                    write to ./backups
#   BACKUP_DIR=/mnt/backups scripts/backup.sh
#   RETAIN_DAYS=14 scripts/backup.sh     keep two weeks instead of thirty days
#
# Intended for cron:
#   0 3 * * *  cd /srv/tradeloom && DATABASE_URL=... scripts/backup.sh >> /var/log/tradeloom-backup.log 2>&1
#
# A dump contains every user's complete trading history. Write it somewhere encrypted, and copy it
# off this machine — a backup that only exists on the server it is backing up is not a backup.
set -euo pipefail

cd "$(dirname "$0")/.."

BACKUP_DIR=${BACKUP_DIR:-./backups}
RETAIN_DAYS=${RETAIN_DAYS:-30}

if [ -z "${DATABASE_URL:-}" ]; then
  echo "error: DATABASE_URL is not set" >&2
  exit 2
fi

# The application speaks postgresql+asyncpg://; libpq does not know that dialect suffix.
PG_URL=${DATABASE_URL/postgresql+asyncpg:/postgresql:}
PG_URL=${PG_URL/postgresql+psycopg:/postgresql:}

case "$PG_URL" in
  postgresql://*) ;;
  *)
    echo "error: DATABASE_URL does not point at PostgreSQL (${DATABASE_URL%%://*}://…)" >&2
    exit 2
    ;;
esac

command -v pg_dump >/dev/null || { echo "error: pg_dump is not installed" >&2; exit 2; }

mkdir -p "$BACKUP_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TARGET="$BACKUP_DIR/tradeloom-$STAMP.dump"

echo "[backup] dumping to $TARGET"
# --format=custom so pg_restore can be selective, and so it compresses.
pg_dump --format=custom --compress=9 --no-owner --no-privileges --file="$TARGET" "$PG_URL"

SIZE=$(du -h "$TARGET" | cut -f1)
echo "[backup] wrote $SIZE"

# A zero-length dump is a failure that exited zero. Catch it here rather than at restore time.
if [ ! -s "$TARGET" ]; then
  echo "[backup] error: the dump is empty" >&2
  rm -f "$TARGET"
  exit 1
fi

echo "[backup] pruning dumps older than ${RETAIN_DAYS} days"
find "$BACKUP_DIR" -name 'tradeloom-*.dump' -type f -mtime "+${RETAIN_DAYS}" -print -delete

echo "[backup] done. Verify it with: scripts/restore-check.sh $TARGET"
