#!/bin/bash
set -e
cd /root/market_bot
git fetch origin
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
if [ "$LOCAL" != "$REMOTE" ]; then
    echo "[$(date)] New commits detected, pulling..."
    git pull origin main --ff-only
    find venv/bin -type f -exec chmod +x {} +
    systemctl restart market-bot
    echo "[$(date)] Restarted market-bot"
else
    echo "[$(date)] Already up to date"
fi
