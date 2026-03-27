#!/bin/bash
# Server-side setup for Phase 6 (Docker Compose + Monitoring).
# Run once on the server: bash /root/market_bot/scripts/server-setup.sh

set -euo pipefail

PROJECT_DIR="/root/market_bot"
cd "$PROJECT_DIR"

echo "=== Phase 6: Server Setup ==="

# 1. Ensure /data directories exist
echo "[1/6] Creating data directories..."
mkdir -p /data/db /data/logs /data/backups

# 2. Move DB to /data/db if it's still in project root
if [ -f "$PROJECT_DIR/market_bot.db" ] && [ ! -f "/data/db/market_bot.db" ]; then
    echo "  Moving DB to /data/db/market_bot.db..."
    cp "$PROJECT_DIR/market_bot.db" /data/db/market_bot.db
    echo "  DB copied. Original kept as backup."
fi

# 3. Deploy nginx config with rate limiting + Grafana
echo "[2/6] Updating nginx config..."
cp "$PROJECT_DIR/scripts/nginx-signabot.conf" /etc/nginx/sites-enabled/miniapp
nginx -t && systemctl reload nginx
echo "  nginx reloaded."

# 4. Stop and disable systemd services (Docker takes over)
echo "[3/6] Migrating from systemd to Docker..."
systemctl stop market-bot market-bot-api 2>/dev/null || true
systemctl disable market-bot market-bot-api 2>/dev/null || true
echo "  Systemd services disabled."

# 5. Build and start Docker Compose
echo "[4/6] Building Docker image (may take a few minutes)..."
docker compose build

echo "[5/6] Starting containers..."
docker compose up -d

echo "  Waiting 10 seconds for services to start..."
sleep 10
docker compose ps

# 6. Set up daily backup cron
echo "[6/6] Setting up backup cron..."
CRON_JOB="0 3 * * * /root/market_bot/scripts/backup.sh >> /data/logs/backup.log 2>&1"
(crontab -l 2>/dev/null | grep -v backup.sh; echo "$CRON_JOB") | crontab -
echo "  Backup cron set: daily at 03:00."

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Services:"
docker compose ps
echo ""
echo "Useful commands:"
echo "  docker compose logs -f market-bot   # bot logs"
echo "  docker compose logs -f api           # api logs"
echo "  docker compose restart market-bot   # restart bot"
echo "  docker compose down && docker compose up -d  # full restart"
echo ""
echo "Grafana:    https://signabot.ru/grafana/ (admin / \$GRAFANA_PASSWORD)"
echo "Prometheus: http://127.0.0.1:9090 (внутри сервера)"
echo "API docs:   https://signabot.ru/api/docs"
