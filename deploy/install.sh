#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/limitadsbot}"
REPOSITORY_URL="${REPOSITORY_URL:-https://github.com/VapeStoreBro/limitadsbot.git}"

apt-get update
apt-get install -y git python3 python3-venv python3-pip docker.io docker-compose-plugin

if [[ ! -d "$PROJECT_DIR/.git" ]]; then
  git clone "$REPOSITORY_URL" "$PROJECT_DIR"
else
  git -C "$PROJECT_DIR" fetch origin main
  git -C "$PROJECT_DIR" reset --hard origin/main
fi

cd "$PROJECT_DIR"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m compileall -q app deploy

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Edit $PROJECT_DIR/.env before starting services."
fi

cp deploy/limitadsbot.service /etc/systemd/system/limitadsbot.service
cp deploy/limitadsbot-deploy.service /etc/systemd/system/limitadsbot-deploy.service
systemctl daemon-reload

echo "Next: edit .env, run docker compose up -d, open TCP 9102, then enable both services."
