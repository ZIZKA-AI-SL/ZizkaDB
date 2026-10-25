#!/usr/bin/env bash
# ZizkaDB OSS quickstart — one container, no git clone, no Python needed.
# Pulls the single ZizkaDB image (Postgres + API + dashboard), starts it, and
# opens the dashboard on a demo agent's causal chain.
# If that image is unavailable, falls back to the multi-container compose stack.
#
#   curl -fsSL https://raw.githubusercontent.com/ZIZKA-AI-SL/ZizkaDB/main/scripts/quickstart-remote.sh | bash
#
# Options (env): ZIZKADB_IMAGE_TAG, ZIZKADB_DASHBOARD_PORT, ZIZKADB_NO_OPEN=1,
#                ZIZKADB_QUICKSTART=compose (force the compose stack)
set -euo pipefail

REF="${ZIZKADB_REF:-main}"
REPO="${ZIZKADB_REPO:-ZIZKA-AI-SL/ZizkaDB}"
BASE="https://raw.githubusercontent.com/${REPO}/${REF}"
INSTALL_DIR="${ZIZKADB_HOME:-${HOME}/.zizkadb}"
INFRA="${INSTALL_DIR}/infra"
CLONE_DIR="${INSTALL_DIR}/repo"

IMAGE="${ZIZKADB_IMAGE:-ghcr.io/zizka-ai-sl/zizkadb:${ZIZKADB_IMAGE_TAG:-latest}}"
CONTAINER="zizkadb"
VOLUME="zizkadb_data"
API_PORT=8000
DASHBOARD_PORT="${ZIZKADB_DASHBOARD_PORT:-3001}"
LEGACY_CONTAINERS="zizkadb_api zizkadb_dashboard zizkadb_postgres zizkadb_qdrant zizkadb_redis"

echo "════════════════════════════════════════════════════════"
echo "  ZizkaDB quickstart"
echo "════════════════════════════════════════════════════════"
echo ""

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: Docker is required. https://docs.docker.com/get-docker/" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker daemon not running. Start Docker Desktop or OrbStack." >&2
  exit 1
fi

telemetry_ping() {
  mkdir -p "${INSTALL_DIR}"
  curl -fsSL "${BASE}/scripts/telemetry-install-ping.sh" -o "${INSTALL_DIR}/telemetry-install-ping.sh" 2>/dev/null \
    && bash "${INSTALL_DIR}/telemetry-install-ping.sh" docker oss || true
}

# ── Single-container path ────────────────────────────────────────────────────

port_in_use() {
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

check_port() {
  local port="$1" what="$2" hint="$3"
  if port_in_use "$port"; then
    echo "ERROR: port ${port} (${what}) is already in use." >&2
    local legacy
    legacy="$(docker ps --format '{{.Names}}' | grep -E '^zizkadb_(api|dashboard)$' || true)"
    if [ -n "$legacy" ]; then
      echo "  An earlier multi-container ZizkaDB stack is running. Stop it with:" >&2
      echo "    docker rm -f ${LEGACY_CONTAINERS}" >&2
      echo "  Its data stays in its Docker volumes; this quickstart starts a new, empty one." >&2
    else
      echo "  Find what is using it:  lsof -nP -iTCP:${port} -sTCP:LISTEN" >&2
      [ -n "$hint" ] && echo "  ${hint}" >&2
    fi
    exit 1
  fi
}

run_single_container() {
  local started=$SECONDS

  echo "→ [1/3] Downloading ZizkaDB (one image; first run only)..."
  if ! docker pull "${IMAGE}"; then
    echo ""
    if docker image inspect "${IMAGE}" >/dev/null 2>&1; then
      echo "→ Could not check for updates — using the copy of ${IMAGE} already on this machine."
    else
      echo "→ ${IMAGE} is not available — using the multi-container stack instead."
      return 1
    fi
  fi

  # Replace a previous quickstart container; data lives in the volume.
  docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
  check_port "${API_PORT}" "API" ""
  check_port "${DASHBOARD_PORT}" "dashboard" "Or pick another: ZIZKADB_DASHBOARD_PORT=3005"

  echo "→ [2/3] Starting..."
  # Local mode (the image defaults to production): one-click dashboard and a
  # well-known API key, so the ports stay on 127.0.0.1 only.
  docker run -d --name "${CONTAINER}" --restart unless-stopped \
    -e ENV=development \
    -p "127.0.0.1:${API_PORT}:8000" \
    -p "127.0.0.1:${DASHBOARD_PORT}:3001" \
    -v "${VOLUME}:/data" \
    ${OPENAI_API_KEY:+-e OPENAI_API_KEY} \
    ${ANTHROPIC_API_KEY:+-e ANTHROPIC_API_KEY} \
    "${IMAGE}" >/dev/null || { echo "ERROR: could not start the ZizkaDB container." >&2; exit 1; }

  echo "→ [3/3] Waiting for the API and dashboard..."
  local i
  for i in $(seq 1 90); do
    if curl -sf "http://localhost:${API_PORT}/health" >/dev/null 2>&1 \
       && curl -sf -o /dev/null "http://localhost:${DASHBOARD_PORT}/login" 2>/dev/null; then
      break
    fi
    if [ "$(docker inspect -f '{{.State.Running}}' "${CONTAINER}" 2>/dev/null)" != "true" ]; then
      echo "ERROR: ZizkaDB stopped during startup. Last log lines:" >&2
      docker logs --tail 30 "${CONTAINER}" >&2 || true
      exit 1
    fi
    if [ "$i" -eq 90 ]; then
      echo "ERROR: ZizkaDB did not become ready in 3 minutes. Check: docker logs ${CONTAINER}" >&2
      exit 1
    fi
    sleep 2
  done

  # The container logs a demo chain on first start; give it a moment to land.
  for i in $(seq 1 10); do
    docker exec "${CONTAINER}" test -f /data/.demo_seeded 2>/dev/null && break
    sleep 1
  done

  telemetry_ping

  local url="http://localhost:${DASHBOARD_PORT}/dashboard/activity?agent=support-bot"
  echo ""
  echo "════════════════════════════════════════════════════════"
  echo "  ZizkaDB is running  (ready in $((SECONDS - started))s)"
  echo "════════════════════════════════════════════════════════"
  if docker exec "${CONTAINER}" test -s /data/demo-chain.txt 2>/dev/null; then
    echo ""
    echo "  Why did the demo agent call lookup_order?"
    echo ""
    docker exec "${CONTAINER}" cat /data/demo-chain.txt | sed 's/^/    /'
  fi
  echo ""
  echo "  Dashboard:  ${url}"
  echo "              click any event → \"Why? (causal)\" tab"
  echo "  API:        http://localhost:${API_PORT}   (key: zizkadb_dev_local)"
  echo "  Connect:    https://github.com/${REPO}/blob/${REF}/CONNECT.md"
  echo ""
  echo "  Stop:       docker stop ${CONTAINER}        Start again: docker start ${CONTAINER}"
  echo "  Logs:       docker logs -f ${CONTAINER}"
  echo "  Upgrade:    re-run this command (your data is kept)"
  echo "  Erase all:  docker rm -f ${CONTAINER} && docker volume rm ${VOLUME}"
  echo ""

  if [ -z "${ZIZKADB_NO_OPEN:-}" ] && [ -z "${CI:-}" ] && [ -t 1 ]; then
    if command -v open >/dev/null 2>&1; then
      open "${url}" >/dev/null 2>&1 || true
    elif command -v xdg-open >/dev/null 2>&1; then
      xdg-open "${url}" >/dev/null 2>&1 || true
    fi
  fi
  return 0
}

# ── Multi-container fallback (compose: Postgres, Qdrant, Redis, API, dashboard) ─

run_demo() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "WARN: python3 not found — stack is up but demo skipped."
    echo "  pip install zizkadb-sdk && zizkadb demo"
    return 0
  fi

  # Install the SDK into a private venv so the demo never depends on (or
  # modifies) the caller's Python: active venvs, pip "user = true" config,
  # PEP 668 externally-managed system Pythons, broken editable installs.
  local venv="${INSTALL_DIR}/venv"
  local py=python3
  echo "→ Installing SDK from PyPI (private venv: ${venv})..."
  if [ -x "${venv}/bin/python" ] || python3 -m venv "$venv" >/dev/null 2>&1; then
    py="${venv}/bin/python"
    PIP_USER=0 "$py" -m pip install -q --disable-pip-version-check --upgrade zizkadb-sdk
  else
    echo "WARN: could not create a venv (python3-venv missing?) — installing into python3."
    PIP_USER=0 python3 -m pip install -q --disable-pip-version-check zizkadb-sdk
  fi

  echo ""
  echo "→ Running causal lineage demo..."
  "$py" -m zizkadb.cli demo || "$py" -c "
import asyncio
from zizkadb import ZizkaDB
async def main():
    async with ZizkaDB(host='http://localhost:8000') as db:
        u = await db.log(agent='support-bot', event='user_message', data={'text': 'Why was my order delayed?'})
        r = await db.log(agent='support-bot', event='llm_response', data={'model': 'gpt-4o'}, parent_id=u.event_id)
        t = await db.log(agent='support-bot', event='tool_call', data={'tool': 'lookup_order'}, parent_id=r.event_id)
        (await db.why(t.event_id)).print()
asyncio.run(main())
"

  echo ""
  echo "════════════════════════════════════════════════════════"
  echo "  Done"
  echo "════════════════════════════════════════════════════════"
  echo ""
  echo "  Dashboard:  http://localhost:3001/dashboard/activity?agent=support-bot"
  echo "  API:        http://localhost:8000/health"
  echo "  Connect:    https://github.com/${REPO}/blob/${REF}/CONNECT.md"
  echo "  Demo again: ${py} -m zizkadb.cli demo"
  echo ""
}

fallback_shallow_clone() {
  echo ""
  echo "→ GHCR pre-built images unavailable — falling back to shallow clone + local build..."
  echo "  (First run may take a few minutes while Docker builds api + dashboard.)"
  echo ""

  if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: git is required for the build fallback." >&2
    echo "  Install git, or run:" >&2
    echo "    git clone https://github.com/${REPO}.git && cd ZizkaDB && bash scripts/quickstart.sh" >&2
    exit 1
  fi

  rm -rf "${CLONE_DIR}"
  git clone --depth 1 --branch "${REF}" "https://github.com/${REPO}.git" "${CLONE_DIR}"
  exec bash "${CLONE_DIR}/scripts/quickstart.sh"
}

run_compose_stack() {
  mkdir -p "${INFRA}"

  echo "→ Downloading compose + schema (not the full repo)..."
  curl -fsSL "${BASE}/infra/docker-compose.quickstart.yml" -o "${INFRA}/docker-compose.quickstart.yml"
  curl -fsSL "${BASE}/core/db/schema.sql" -o "${INFRA}/schema.sql"
  curl -fsSL "${BASE}/infra/.env.quickstart" -o "${INFRA}/.env"

  echo "→ Pulling pre-built images from ghcr.io/zizka-ai-sl/ (first run may take 5–10 min)..."
  cd "${INFRA}"
  if ! docker compose -f docker-compose.quickstart.yml pull; then
    fallback_shallow_clone
  fi

  echo "→ Starting stack (postgres, qdrant, redis, api, dashboard)..."
  # Recreate so containers from a clone-based setup (same project "infra") never
  # keep a stale network reference. Data lives in volumes.
  docker compose -f docker-compose.quickstart.yml up -d --force-recreate --remove-orphans

  echo "→ Waiting for API..."
  for i in $(seq 1 60); do
    if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
      break
    fi
    sleep 2
    if [ "$i" -eq 60 ]; then
      echo "ERROR: API did not become healthy. Check: docker compose -f ${INFRA}/docker-compose.quickstart.yml logs api" >&2
      exit 1
    fi
  done

  telemetry_ping

  run_demo

  echo "  Stop:  cd ${INFRA} && docker compose -f docker-compose.quickstart.yml down"
  echo ""
}

if [ "${ZIZKADB_QUICKSTART:-}" = "compose" ] || ! run_single_container; then
  run_compose_stack
fi
