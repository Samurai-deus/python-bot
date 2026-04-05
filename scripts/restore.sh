#!/bin/bash
# Restore database from backup.
# Supports both SQLite (.db.gz) and PostgreSQL (.sql.gz) backups.
#
# Usage:
#   ./scripts/restore.sh /data/backups/market_bot_pg_20260405.sql.gz
#   ./scripts/restore.sh /data/backups/market_bot_20260405.db.gz
#
# IMPORTANT: Stop bot and API before restoring!
#   docker compose stop market-bot api

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <backup_file>"
    echo ""
    echo "Examples:"
    echo "  $0 /data/backups/market_bot_pg_20260405_030000.sql.gz   # PostgreSQL"
    echo "  $0 /data/backups/market_bot_20260405_030000.db.gz       # SQLite"
    echo ""
    echo "Available backups:"
    ls -lht /data/backups/market_bot_*.gz 2>/dev/null || echo "  (none found)"
    exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "[$(date)] ERROR: Backup file not found: $BACKUP_FILE"
    exit 1
fi

# Verify gzip integrity
echo "[$(date)] Verifying backup integrity..."
if ! gzip -t "$BACKUP_FILE" 2>/dev/null; then
    echo "[$(date)] ERROR: Backup file is corrupted: $BACKUP_FILE"
    exit 1
fi
echo "[$(date)] Integrity check passed"

if [[ "$BACKUP_FILE" == *".sql.gz" ]]; then
    # ========== PostgreSQL restore ==========
    if [ -z "${DATABASE_URL:-}" ]; then
        echo "[$(date)] ERROR: DATABASE_URL not set. Cannot restore PostgreSQL backup."
        exit 1
    fi

    echo "[$(date)] Restoring PostgreSQL from: $BACKUP_FILE"
    echo "[$(date)] WARNING: This will REPLACE all data in the database!"
    echo "[$(date)] Press Ctrl+C within 5 seconds to cancel..."
    sleep 5

    # Restore: drop existing objects and recreate
    gunzip -c "$BACKUP_FILE" | psql "$DATABASE_URL" --single-transaction --set ON_ERROR_STOP=on

    echo "[$(date)] PostgreSQL restore complete"

elif [[ "$BACKUP_FILE" == *".db.gz" ]]; then
    # ========== SQLite restore ==========
    DB_PATH="${DB_PATH:-/data/db/market_bot.db}"

    echo "[$(date)] Restoring SQLite to: $DB_PATH"
    echo "[$(date)] WARNING: This will REPLACE the current database!"
    echo "[$(date)] Press Ctrl+C within 5 seconds to cancel..."
    sleep 5

    # Backup current DB before overwriting
    if [ -f "$DB_PATH" ]; then
        PRE_RESTORE="/data/backups/pre_restore_$(date +%Y%m%d_%H%M%S).db"
        cp "$DB_PATH" "$PRE_RESTORE"
        echo "[$(date)] Current DB saved to: $PRE_RESTORE"
    fi

    gunzip -c "$BACKUP_FILE" > "$DB_PATH"

    # Verify restored DB
    if sqlite3 "$DB_PATH" "SELECT count(*) FROM trades;" >/dev/null 2>&1; then
        echo "[$(date)] SQLite restore complete and verified"
    else
        echo "[$(date)] ERROR: Restored DB verification failed!"
        if [ -f "${PRE_RESTORE:-}" ]; then
            cp "$PRE_RESTORE" "$DB_PATH"
            echo "[$(date)] Rolled back to previous DB"
        fi
        exit 1
    fi
else
    echo "[$(date)] ERROR: Unknown backup format. Expected .sql.gz or .db.gz"
    exit 1
fi

echo "[$(date)] Done. Restart services: docker compose start market-bot api"
