#!/usr/bin/env bash
# Restart ZizkaDB locally WITHOUT Docker (stop native processes, then start).
# Fallback when container runtime is unavailable. Not used in production deploy.
# Requires: local Postgres (zizkadb DB), Redis, Python venv, Node 20+.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Prefer Node 20 from nvm when available
if [ -d "${HOME}/.nvm/versions/node" ]; then
  NODE20="$(ls -d "${HOME}/.nvm/versions/node"/v20.* 2>/dev/null | tail -1 || true)"
  if [ -n "$NODE20" ]; then
    export PATH="${NODE20}/bin:${PATH}"
  fi
fi

if [ ! -f infra/.env ]; then
  bash scripts/bootstrap-local.sh
fi

if ! .venv/bin/python -c "import zizkadb" 2>/dev/null; then
  echo "→ SDK not installed; running bootstrap"
  bash scripts/bootstrap-local.sh
fi

mkdir -p .local/qdrant/storage .local/pids .local/logs

echo "→ Stopping any existing native stack processes"
bash scripts/stop-native-stack.sh

# Qdrant
if ! curl -sf http://localhost:6333/ >/dev/null 2>&1; then
  if [ ! -x .local/qdrant/qdrant ]; then
    bash scripts/download-qdrant.sh
  fi
  echo "→ Starting Qdrant on :6333"
  (
    cd .local/qdrant
    QDRANT__STORAGE__STORAGE_PATH=./storage \
      nohup ./qdrant > ../logs/qdrant.log 2>&1 &
    echo $! > ../pids/qdrant.pid
  )
fi

# API
if ! curl -sf http://localhost:8000/health >/dev/null 2>&1; then
  echo "→ Starting API on :8000"
  (
    cd core
    set -a && source ../infra/.env && set +a
    # The native Redis and Qdrant run without auth; the passwords in infra/.env
    # are for the Docker stack.
    unset REDIS_PASSWORD QDRANT_API_KEY
    nohup ../.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 \
      > ../.local/logs/api.log 2>&1 &
    echo $! > ../.local/pids/api.pid
  )
fi

# Dashboard (production build must exist)
if ! curl -sf -o /dev/null http://127.0.0.1:3001/login 2>/dev/null; then
  if [ ! -d dashboard/.next ]; then
    echo "→ Building dashboard (first time, may take several minutes)"
    (
      cd dashboard
      NEXT_PUBLIC_API_URL=http://localhost:8000 NEXT_PUBLIC_DEV_MODE=true \
        NEXT_PUBLIC_DEPLOYMENT_MODE=self_hosted npm run build
    )
  fi
  echo "→ Starting dashboard on :3001"
  (
    cd dashboard
    nohup npx next start -p 3001 -H 127.0.0.1 > ../.local/logs/dashboard.log 2>&1 &
    echo $! > ../.local/pids/dashboard.pid
  )
fi

echo "Waiting for services..."
for i in $(seq 1 30); do
  curl -sf http://localhost:8000/health >/dev/null 2>&1 && break
  sleep 2
done

echo ""
echo "  API:        http://localhost:8000/health"
echo "  Dashboard:  http://localhost:3001/login"
echo "  Smoke:      bash scripts/smoke-test.sh"
echo "  Stop:       bash scripts/stop-native-stack.sh"
echo "  Logs:       .local/logs/"
