#!/bin/bash
# Docker-based deploy script.
# Usage: ./scripts/deploy.sh [--first-run]
# --first-run: stop systemd services and migrate to Docker

set -euo pipefail

COMPOSE="docker compose"
PROJECT_DIR="/root/market_bot"

cd "$PROJECT_DIR"

echo "[$(date)] Pulling latest code..."
git fetch origin main
git reset --hard origin/main

# On first run, stop and disable systemd services
if [[ "${1:-}" == "--first-run" ]]; then
    echo "[$(date)] First run: stopping systemd services..."
    systemctl stop market-bot market-bot-api 2>/dev/null || true
    systemctl disable market-bot market-bot-api 2>/dev/null || true
    echo "[$(date)] Systemd services disabled."
fi

echo "[$(date)] Building Docker image..."
$COMPOSE build

echo "[$(date)] Starting services..."
$COMPOSE up -d

echo "[$(date)] Waiting for services to be healthy..."
sleep 5

echo "[$(date)] Container status:"
$COMPOSE ps

echo "[$(date)] Deploy complete."
echo ""
echo "Logs:"
echo "  docker compose logs -f market-bot"
echo "  docker compose logs -f api"
