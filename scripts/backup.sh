#!/bin/bash
# Daily SQLite backup with rotation.
# Usage: ./scripts/backup.sh
# Cron: 0 3 * * * /root/market_bot/scripts/backup.sh >> /data/logs/backup.log 2>&1

set -euo pipefail

DB_PATH="${DB_PATH:-/data/db/market_bot.db}"
BACKUP_DIR="/data/backups"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/market_bot_$DATE.db"
KEEP_DAYS=7

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DB_PATH" ]; then
    echo "[$(date)] ERROR: DB not found at $DB_PATH"
    exit 1
fi

echo "[$(date)] Starting backup: $DB_PATH → ${BACKUP_FILE}.gz"

# VACUUM INTO is safe for live WAL-mode SQLite (no lock needed)
sqlite3 "$DB_PATH" "VACUUM INTO '$BACKUP_FILE'"

gzip "$BACKUP_FILE"

SIZE=$(du -sh "${BACKUP_FILE}.gz" | cut -f1)
echo "[$(date)] Backup done: ${BACKUP_FILE}.gz ($SIZE)"

# Rotate: delete backups older than KEEP_DAYS
DELETED=$(find "$BACKUP_DIR" -name "market_bot_*.db.gz" -mtime +"$KEEP_DAYS" -print -delete | wc -l)
if [ "$DELETED" -gt 0 ]; then
    echo "[$(date)] Rotated $DELETED old backup(s)"
fi
