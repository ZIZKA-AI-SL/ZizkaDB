#!/usr/bin/env bash
# Start local ZizkaDB: API + dashboard with dev login enabled.
# Pulls pre-built GHCR images when available; otherwise builds locally.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SHOW_BANNER=1
for arg in "$@"; do
  case "$arg" in
    --no-banner) SHOW_BANNER=0 ;;
  esac
done

if [ "$SHOW_BANNER" -eq 1 ]; then
  echo "→ ZizkaDB local setup"
  echo ""
fi

if [ "$(uname -m)" = "x86_64" ] && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ]; then
  echo "WARN: Rosetta shell detected. Docker may fail. Use native arm64 Terminal or:"
  echo "  bash scripts/restart-native-stack.sh"
  echo ""
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: Docker is required. Install from https://docs.docker.com/get-docker/" >&2
  echo "  Fallback (no Docker): bash scripts/bootstrap-local.sh && bash scripts/restart-native-stack.sh" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker daemon not running." >&2
  echo "  Start OrbStack or Docker Desktop, or use native fallback:" >&2
  echo "  bash scripts/restart-native-stack.sh" >&2
  exit 1
fi

if [ ! -f infra/.env ]; then
  cp .env.example infra/.env
  echo "✓ Created infra/.env from .env.example"
  echo "  Tip: add OPENAI_API_KEY for semantic search (logging works without it)"
else
  echo "✓ Using existing infra/.env"
  if grep -q '^DEV_API_KEY=agdb_dev_local' infra/.env 2>/dev/null; then
    if [[ "$(uname)" == "Darwin" ]]; then
      sed -i.bak 's/^DEV_API_KEY=agdb_dev_local/DEV_API_KEY=zizkadb_dev_local/' infra/.env
      rm -f infra/.env.bak
    else
      sed -i 's/^DEV_API_KEY=agdb_dev_local/DEV_API_KEY=zizkadb_dev_local/' infra/.env
    fi
    echo "✓ Migrated DEV_API_KEY agdb_dev_local → zizkadb_dev_local"
  fi
fi

# infra/docker-compose.yml requires ENV and .env.example leaves it unset so a
# server copy cannot default to development. This script is for a local
# machine, so choose development unless infra/.env already sets ENV.
if ! grep -Eq '^[[:space:]]*(export[[:space:]]+)?ENV[[:space:]]*=' infra/.env 2>/dev/null; then
  printf '\n# Added by setup-local.sh (local machine only; never on a server)\nENV=development\n' >> infra/.env
  echo "✓ Set ENV=development in infra/.env (local machine)"
fi

COMPOSE=( -f infra/docker-compose.yml -f infra/docker-compose.dashboard.yml )
IMAGE_TAG="${ZIZKADB_IMAGE_TAG:-latest}"
USE_PREBUILT="${ZIZKADB_USE_PREBUILT_IMAGES:-auto}"
BUILD_FLAG=( --build )

if [ "$USE_PREBUILT" = "0" ]; then
  echo "→ Building images locally (ZIZKADB_USE_PREBUILT_IMAGES=0)"
elif docker manifest inspect "ghcr.io/zizka-ai-sl/zizkadb-api:${IMAGE_TAG}" >/dev/null 2>&1; then
  COMPOSE+=( -f infra/docker-compose.oss.yml )
  BUILD_FLAG=()
  echo "→ Using pre-built GHCR images (tag: ${IMAGE_TAG})"
elif [ "$USE_PREBUILT" = "1" ]; then
  echo "ERROR: ZIZKADB_USE_PREBUILT_IMAGES=1 but ghcr.io/zizka-ai-sl/zizkadb-api:${IMAGE_TAG} not found." >&2
  echo "  Publish images first, or unset ZIZKADB_USE_PREBUILT_IMAGES." >&2
  exit 1
else
  echo "→ Pre-built images not found — building locally (first run may take a few minutes)"
fi

if [ ${#BUILD_FLAG[@]} -gt 0 ]; then
  # Dev overlay: mounts host core/ into the container and enables --reload so
  # local edits are reflected without a rebuild. Only used when building from
  # source; pre-built OSS images are immutable and don't need the mount.
  COMPOSE+=( -f infra/docker-compose.dev.yml )
  # Registry metadata checks during `compose build` occasionally hang/timeout
  # (DeadlineExceeded) even when the base image is already cached locally.
  # Pre-pulling once avoids that flake and speeds up every subsequent build.
  echo "→ Warming base image cache..."
  docker pull python:3.12-slim >/dev/null 2>&1 || true
  docker pull node:20-alpine >/dev/null 2>&1 || true
fi

echo "→ Starting API + Postgres + Qdrant + Redis + Dashboard..."
# ${arr[@]+...}: an empty array is "unbound" under set -u on macOS bash 3.2.
# --force-recreate/--remove-orphans: the quickstart (~/.zizkadb) and a clone
# share the compose project "infra"; recreating avoids containers left pointing
# at a network the other setup deleted ("network ... not found"). Data lives in
# volumes, so nothing is lost.
docker compose "${COMPOSE[@]}" up -d --force-recreate --remove-orphans ${BUILD_FLAG[@]+"${BUILD_FLAG[@]}"}

echo "→ Waiting for API health..."
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
  if [ "$i" -eq 30 ]; then
    echo "ERROR: API did not become healthy. Check: docker compose -f infra/docker-compose.yml logs api" >&2
    exit 1
  fi
done

# Count OSS Docker install (anonymous; separate from PyPI download stats).
bash scripts/telemetry-install-ping.sh docker oss || true

if [ "$SHOW_BANNER" -eq 0 ]; then
  exit 0
fi

echo ""
echo "════════════════════════════════════════════════════════"
echo "  ZizkaDB is running locally"
echo "════════════════════════════════════════════════════════"
echo ""
echo "  API:        http://localhost:8000/health"
echo "  Swagger:    http://localhost:8000/swagger"
echo "  Dashboard:  http://localhost:3001/dashboard/activity?agent=support-bot"
echo "              (login: http://localhost:3001/login → Open my dashboard →)"
echo ""
echo "  Quick win:  bash scripts/quickstart.sh   # stack + db.why() demo"
echo ""
echo "  Next steps:"
echo "  1. Open http://localhost:3001/dashboard/activity?agent=support-bot"
echo "  2. Click \"Open my dashboard →\""
echo "  3. Run the lineage demo:"
echo ""
echo "     pip install zizkadb-sdk"
echo "     zizkadb demo"
echo ""
echo "  4. Connect your agent: see CONNECT.md"
echo ""
echo "  Stop:  docker compose -f infra/docker-compose.yml -f infra/docker-compose.dashboard.yml down"
echo "  Reset local DB (dev only): bash scripts/reset-local-db.sh"
echo "  Smoke: bash scripts/smoke-test.sh"
echo "  Example: bash scripts/smoke-example.sh"
echo "  No Docker?  bash scripts/bootstrap-local.sh && bash scripts/restart-native-stack.sh"
echo ""
