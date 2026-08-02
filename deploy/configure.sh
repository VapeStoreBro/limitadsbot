#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/limitadsbot}"
cd "$PROJECT_DIR"

if [[ -f .env ]]; then
  echo "$PROJECT_DIR/.env already exists. It was not overwritten."
  echo "Edit it manually or remove it only if this is a fresh installation."
  exit 1
fi

read -rsp "Telegram bot token: " BOT_TOKEN
echo
if [[ ! "$BOT_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
  echo "The Telegram token format looks invalid." >&2
  exit 1
fi

POSTGRES_PASSWORD="$(openssl rand -hex 24)"
GITHUB_WEBHOOK_SECRET="$(openssl rand -hex 32)"
DEPLOY_PATH_SECRET="$(openssl rand -hex 24)"
TELEGRAM_WEBHOOK_SECRET="$(openssl rand -hex 32)"
TELEGRAM_PATH_SECRET="$(openssl rand -hex 24)"

cat > .env <<EOF
BOT_TOKEN=$BOT_TOKEN
OWNER_ID=6577441312
BAZAAR_CHAT_ID=-1003377593526
STAFF_CHAT_ID=-5466156820
TIMEZONE=Europe/Moscow
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
DATABASE_URL=postgresql+asyncpg://limitads:$POSTGRES_PASSWORD@127.0.0.1:5433/limitads
WEBHOOK_BASE_URL=
TELEGRAM_WEBHOOK_PATH=/telegram/$TELEGRAM_PATH_SECRET
TELEGRAM_WEBHOOK_SECRET=$TELEGRAM_WEBHOOK_SECRET
WEB_SERVER_HOST=127.0.0.1
WEB_SERVER_PORT=8092
GITHUB_REPOSITORY=VapeStoreBro/limitadsbot
GITHUB_WEBHOOK_SECRET=$GITHUB_WEBHOOK_SECRET
DEPLOY_PATH_SECRET=$DEPLOY_PATH_SECRET
DEPLOY_HOST=0.0.0.0
DEPLOY_PORT=9102
PROJECT_DIR=/root/limitadsbot
SYSTEMD_SERVICE=limitadsbot.service
EOF
chmod 600 .env

# Start the isolated PostgreSQL container and wait until it is ready.
docker compose up -d postgres
for attempt in {1..30}; do
  if docker compose exec -T postgres pg_isready -U limitads -d limitads >/dev/null 2>&1; then
    break
  fi
  if [[ "$attempt" == "30" ]]; then
    echo "PostgreSQL did not become ready in time." >&2
    docker compose logs --tail=100 postgres
    exit 1
  fi
  sleep 2
done

cp deploy/limitadsbot.service /etc/systemd/system/limitadsbot.service
cp deploy/limitadsbot-deploy.service /etc/systemd/system/limitadsbot-deploy.service
systemctl daemon-reload
systemctl enable --now limitadsbot.service limitadsbot-deploy.service

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
  ufw allow 9102/tcp >/dev/null
fi

sleep 3

echo
echo "========== GitHub webhook settings =========="
echo "Payload URL: http://195.133.9.214:9102/deploy/$DEPLOY_PATH_SECRET"
echo "Content type: application/json"
echo "Secret: $GITHUB_WEBHOOK_SECRET"
echo "Events: Just the push event"
echo "============================================="
echo
echo "Service status:"
systemctl --no-pager --full status limitadsbot.service limitadsbot-deploy.service || true
