#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
npm run build
rsync -avz --delete dist/ root@45.150.38.138:/var/www/miniapp/
ssh root@45.150.38.138 "chown -R www-data:www-data /var/www/miniapp 2>/dev/null || true"
echo "Deployed to https://signabot.ru"
