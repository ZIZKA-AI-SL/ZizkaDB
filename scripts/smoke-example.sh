#!/usr/bin/env bash
# Run the minimal Python example against a running ZizkaDB API.
#
# Usage:
#   bash scripts/smoke-example.sh [API_BASE_URL]
#
# Prerequisites:
#   bash scripts/setup-local.sh   (or any stack with ENV=development on :8000)
#
# Verifies: log → parent_id → why() against the live API.

set -euo pipefail

# Test runs are not installs: no telemetry ping unless asked for.
export ZIZKADB_TELEMETRY="${ZIZKADB_TELEMETRY:-false}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${1:-http://localhost:8000}"
BASE="${BASE%/}"
EXAMPLE_DIR="$ROOT/examples/minimal-python"

echo "→ Health ($BASE/health)"
curl -sf "$BASE/health" | grep -q '"status":"ok"'

if [ -f "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
  "$PY" -m pip install -q -e "$ROOT/sdk/python"
else
  PY="${PYTHON:-python3}"
  "$PY" -m pip install -q -r "$EXAMPLE_DIR/requirements.txt"
fi

echo "→ Running examples/minimal-python/agent.py"
(
  cd "$EXAMPLE_DIR"
  export ZIZKADB_HOST="$BASE"
  exec "$PY" agent.py
)

echo "✓ Example agent completed ($BASE)"
