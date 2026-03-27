#!/bin/bash
# Market Bot deploy script — Docker Compose edition
# Run manually: bash /root/market_bot/deploy.sh
# Or via cron: 0 * * * * /root/market_bot/deploy.sh >> /data/logs/deploy.log 2>&1
set -euo pipefail

cd /root/market_bot

git fetch origin
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    echo "[$(date)] Already up to date"
    exit 0
fi

echo "[$(date)] New commits detected, deploying..."

# Hard reset — always matches remote exactly (no merge conflicts)
git reset --hard origin/main

# Warn if WS auth is disabled in production
if grep -qE "^DISABLE_WS_AUTH=true" .env 2>/dev/null; then
    echo "[$(date)] WARNING: DISABLE_WS_AUTH=true in .env — WS is unprotected!"
fi

# Build new image and restart containers
docker compose build --no-cache
docker compose up -d

# Remove dangling images to free disk space
docker image prune -f

# Clean up old logs (keep 7 days)
find /data/logs -name "*.log" -mtime +7 -delete 2>/dev/null || true
find /data/logs -name "*.log.*" -mtime +7 -delete 2>/dev/null || true

echo "[$(date)] Deploy complete ($(git rev-parse --short HEAD))"
