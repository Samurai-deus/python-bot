#!/bin/bash
# Daily database backup with rotation.
# Supports both SQLite and PostgreSQL (auto-detected via DATABASE_URL).
# Usage: ./scripts/backup.sh
# Cron: 0 3 * * * /root/market_bot/scripts/backup.sh >> /data/logs/backup.log 2>&1

set -euo pipefail

BACKUP_DIR="/data/backups"
KEEP_DAYS=30
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

if [ -n "${DATABASE_URL:-}" ]; then
    # ========== PostgreSQL backup ==========
    BACKUP_FILE="$BACKUP_DIR/market_bot_pg_$DATE.sql.gz"
    echo "[$(date)] Starting PostgreSQL backup → $BACKUP_FILE"

    pg_dump "$DATABASE_URL" --no-owner --no-acl | gzip > "$BACKUP_FILE"

    SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
    echo "[$(date)] PostgreSQL backup done: $BACKUP_FILE ($SIZE)"

    # Verify: gzip integrity check
    if ! gzip -t "$BACKUP_FILE" 2>/dev/null; then
        echo "[$(date)] ERROR: Backup integrity check failed: $BACKUP_FILE"
        exit 1
    fi
    echo "[$(date)] Integrity check passed"

    # Rotate PostgreSQL backups
    DELETED=$(find "$BACKUP_DIR" -name "market_bot_pg_*.sql.gz" -mtime +"$KEEP_DAYS" -print -delete | wc -l)
else
    # ========== SQLite backup ==========
    DB_PATH="${DB_PATH:-/data/db/market_bot.db}"
    BACKUP_FILE="$BACKUP_DIR/market_bot_$DATE.db"

    if [ ! -f "$DB_PATH" ]; then
        echo "[$(date)] ERROR: DB not found at $DB_PATH"
        exit 1
    fi

    echo "[$(date)] Starting SQLite backup: $DB_PATH → ${BACKUP_FILE}.gz"

    # VACUUM INTO is safe for live WAL-mode SQLite (no lock needed)
    sqlite3 "$DB_PATH" "VACUUM INTO '$BACKUP_FILE'"
    gzip "$BACKUP_FILE"
    BACKUP_FILE="${BACKUP_FILE}.gz"

    SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
    echo "[$(date)] SQLite backup done: $BACKUP_FILE ($SIZE)"

    # Rotate SQLite backups
    DELETED=$(find "$BACKUP_DIR" -name "market_bot_*.db.gz" -mtime +"$KEEP_DAYS" -print -delete | wc -l)
fi

if [ "$DELETED" -gt 0 ]; then
    echo "[$(date)] Rotated $DELETED old backup(s)"
fi
