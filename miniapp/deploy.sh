#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

DEPLOY_HOST="${DEPLOY_HOST:?Set DEPLOY_HOST env var (e.g. export DEPLOY_HOST=your.server.ip)}"
DEPLOY_USER="${DEPLOY_USER:-root}"

npm run build
rsync -avz --delete dist/ "${DEPLOY_USER}@${DEPLOY_HOST}:/var/www/miniapp/"
ssh "${DEPLOY_USER}@${DEPLOY_HOST}" "chown -R www-data:www-data /var/www/miniapp 2>/dev/null || true"
echo "Deployed to https://signabot.ru"
