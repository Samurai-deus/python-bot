#!/bin/bash
# Disk cleanup script — runs daily to prevent /data disk exhaustion.
# Cron: 30 3 * * * /root/market_bot/scripts/cleanup_disk.sh >> /data/logs/cleanup.log 2>&1

set -euo pipefail

LOG_DIR="${LOG_DIR:-/data/logs}"
KEEP_LOG_DAYS="${KEEP_LOG_DAYS:-7}"
KEEP_BACKUP_DAYS="${KEEP_BACKUP_DAYS:-7}"

echo "[$(date)] Starting disk cleanup"

# --- Application logs (keep 7 days) ---
if [ -d "$LOG_DIR" ]; then
    DELETED_LOGS=$(find "$LOG_DIR" -name "*.log" -mtime +"$KEEP_LOG_DAYS" -print -delete 2>/dev/null | wc -l)
    DELETED_ROTATED=$(find "$LOG_DIR" -name "*.log.*" -mtime +"$KEEP_LOG_DAYS" -print -delete 2>/dev/null | wc -l)
    echo "[$(date)] Deleted logs: $DELETED_LOGS application, $DELETED_ROTATED rotated"
fi

# --- SQLite WAL/SHM files orphaned by crash ---
find /data/db -name "*.db-wal" -size +50M -print 2>/dev/null | while read f; do
    echo "[$(date)] Large WAL file detected: $f ($(du -sh "$f" | cut -f1))"
done

# --- Docker: remove dangling images and stopped containers ---
docker image prune -f 2>/dev/null && echo "[$(date)] Docker dangling images pruned" || true
docker container prune -f 2>/dev/null && echo "[$(date)] Docker stopped containers pruned" || true

# --- Docker logs (json-file driver already rotated via compose, but truncate if huge) ---
find /var/lib/docker/containers -name "*.log" -size +100M -print 2>/dev/null | while read f; do
    echo "[$(date)] Truncating oversized Docker log: $f"
    truncate -s 0 "$f"
done

# --- Report disk usage ---
echo "[$(date)] Disk usage after cleanup:"
df -h /data 2>/dev/null || df -h /
echo "[$(date)] Cleanup complete"
