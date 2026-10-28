#!/usr/bin/env bash
# Consistent SQLite backup for EnglishForge.
#
# Uses SQLite's online backup API (".backup"), which is safe while the backend
# is running — unlike copying the .db file, which can capture a torn WAL.
#
# Usage:
#   ./scripts/backup_sqlite.sh [DB_PATH] [OUT_DIR]
#
# Defaults: backend/data/englishforge.db → backups/
# Keeps the last 14 backups; copy them off-host for real disaster recovery.
set -euo pipefail

DB_PATH="${1:-backend/data/englishforge.db}"
OUT_DIR="${2:-backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "error: sqlite3 is required (apt install sqlite3 / brew install sqlite)" >&2
  exit 1
fi

if [[ ! -f "$DB_PATH" ]]; then
  echo "error: database not found: $DB_PATH" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="$OUT_DIR/englishforge-$STAMP.db"

sqlite3 "$DB_PATH" ".backup '$TARGET'"
# Fail loudly if the copy is not a healthy database.
if [[ "$(sqlite3 "$TARGET" 'PRAGMA integrity_check;')" != "ok" ]]; then
  echo "error: backup failed integrity check: $TARGET" >&2
  rm -f "$TARGET"
  exit 1
fi

echo "backup written: $TARGET ($(du -h "$TARGET" | cut -f1))"

find "$OUT_DIR" -maxdepth 1 -name 'englishforge-*.db' -type f -mtime "+$RETENTION_DAYS" -print -delete
