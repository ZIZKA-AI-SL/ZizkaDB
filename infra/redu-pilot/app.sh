#!/usr/bin/env bash
# Runs ZizkaDB's single container on a small Ubuntu VM (Redu's website pilot:
# 1 vCPU, 2 GB RAM, 10 GB disk) against a database on another VM. Safe to run
# again: it updates the container and keeps /data (secrets, Redis) and the
# database untouched.
#
# Usage (as root, or as a user with sudo):
#   DATABASE_URL=postgresql://zizkadb:<password>@<db private IP>:5432/zizkadb \
#   ZIZKADB_PUBLIC_URL=https://<published URL> bash app.sh
#
# Optional:
#   ZIZKADB_IMAGE      prebuilt image (default ghcr.io/zizka-ai-sl/zizkadb:latest)
#   ZIZKADB_BUILD_REF  build from this git branch or tag instead of pulling
#   TRUSTED_PROXIES    proxy addresses allowed to set X-Forwarded-For
#   EVENT_RETENTION_DAYS  delete events older than this many days
set -euo pipefail

[ "$(id -u)" -eq 0 ] || exec sudo -E bash "$0" "$@"

: "${DATABASE_URL:?Set DATABASE_URL to the database VM address (see infra/redu-db/setup.sh)}"
IMAGE="${ZIZKADB_IMAGE:-ghcr.io/zizka-ai-sl/zizkadb:latest}"
REPO=https://github.com/ZIZKA-AI-SL/ZizkaDB.git
NAME=zizkadb
ENV_FILE=/etc/zizkadb/app.env

log() { echo "[zizkadb-app] $*"; }
free_mb() { df -Pm / | awk 'NR==2 {print $4}'; }

if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker ($(free_mb) MB free on /)..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q --no-install-recommends docker.io ca-certificates git
  apt-get clean
  rm -rf /var/lib/apt/lists/*
  systemctl enable --now docker >/dev/null
fi

# Settings go to a root-only file passed with --env-file, so the database
# password is never on a command line or in this script's output.
install -d -m 700 /etc/zizkadb
umask 077
{
  echo "ENV=production"
  echo "DATABASE_URL=${DATABASE_URL}"
  echo "WEB_CONCURRENCY=1"
  echo "TRUSTED_PROXIES=${TRUSTED_PROXIES:-127.0.0.1,::1,10.0.0.0/8}"
  [ -n "${ZIZKADB_PUBLIC_URL:-}" ] && echo "ZIZKADB_PUBLIC_URL=${ZIZKADB_PUBLIC_URL}"
  [ -n "${EVENT_RETENTION_DAYS:-}" ] && echo "EVENT_RETENTION_DAYS=${EVENT_RETENTION_DAYS}"
  true
} > "${ENV_FILE}.new"
mv "${ENV_FILE}.new" "$ENV_FILE"
umask 022

if [ -n "${ZIZKADB_BUILD_REF:-}" ]; then
  log "Building ${ZIZKADB_BUILD_REF} from source (about 10 minutes on 1 vCPU)..."
  src="$(mktemp -d)"
  git clone -q --depth 1 --branch "$ZIZKADB_BUILD_REF" "$REPO" "$src"
  IMAGE=zizkadb:local
  docker build -q -f "$src/infra/allinone/Dockerfile" -t "$IMAGE" "$src" >/dev/null
  rm -rf "$src"
  docker builder prune -af >/dev/null 2>&1 || true
else
  log "Pulling ${IMAGE}..."
  docker pull -q "$IMAGE" >/dev/null
fi

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --restart unless-stopped \
  --env-file "$ENV_FILE" -p 3001:3001 -v zizkadb_data:/data "$IMAGE" >/dev/null
docker image prune -f >/dev/null 2>&1 || true

log "Waiting for ZizkaDB to start..."
for _ in $(seq 1 90); do
  if curl -fsS -o /dev/null http://127.0.0.1:3001/health/deep; then
    log "Running on port 3001 ($(free_mb) MB free on /)."
    log "Admin token: sudo docker exec ${NAME} sed -n 's/^GEN_SELFHOST_ADMIN_TOKEN=//p' /data/secrets.env"
    exit 0
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" != true ]; then
    break
  fi
  sleep 2
done
log "ZizkaDB did not become healthy. Last log lines (no secrets are logged):"
docker logs --tail 30 "$NAME" 2>&1 | sed 's/^/  /'
exit 1
