#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/limitadsbot}"
REPOSITORY_URL="${REPOSITORY_URL:-https://github.com/VapeStoreBro/limitadsbot.git}"

apt-get update
apt-get install -y git python3 python3-venv python3-pip openssl

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install Docker Engine from the official Docker repository, then rerun this script." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is missing. Install docker-compose-plugin from the same Docker repository, then rerun this script." >&2
  exit 1
fi

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
  echo "Environment is not configured yet. Run: bash deploy/configure.sh"
fi

cp deploy/limitadsbot.service /etc/systemd/system/limitadsbot.service
cp deploy/limitadsbot-deploy.service /etc/systemd/system/limitadsbot-deploy.service
systemctl daemon-reload

echo "Installation completed. Next: bash deploy/configure.sh"
