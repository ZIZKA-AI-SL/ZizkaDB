#!/usr/bin/env bash
# Smoke test against a running ZizkaDB stack.
#
# Usage:
#   bash scripts/smoke-test.sh [API_BASE_URL]
#
# Local (development):
#   bash scripts/smoke-test.sh http://localhost:8000
#   Uses DEV_API_KEY / zizkadb_dev_local and /v1/auth/dev-token when available.
#
# Staging / production-like (ENV=production):
#   ZIZKADB_API_KEY='zizkadb_live_...' bash scripts/smoke-test.sh https://staging-db.zizka.ai
#   Requires a real API key — dev-token is disabled when ENV=production.
#
# Optional:
#   SKIP_SEARCH=1          skip semantic search check
#   SKIP_DEEP_HEALTH=1     skip /health/deep (not recommended)
#   SKIP_EXAMPLE=1         skip minimal-python example (curl-only smoke)

set -euo pipefail

BASE="${1:-http://localhost:8000}"
BASE="${BASE%/}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$ROOT/infra/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/infra/.env"
  set +a
fi

API_KEY="${ZIZKADB_API_KEY:-${SMOKE_API_KEY:-}}"
DEV_KEY="${DEV_API_KEY:-zizkadb_dev_local}"

echo "→ Health ($BASE/health)"
curl -sf "$BASE/health" | grep -q '"status":"ok"'

if [ "${SKIP_DEEP_HEALTH:-}" != "1" ]; then
  echo "→ Deep health ($BASE/health/deep)"
  deep="$(curl -s "$BASE/health/deep")"
  if ! echo "$deep" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    echo "✗ /health/deep is not ok:" >&2
    echo "$deep" >&2
    exit 1
  fi
fi

if [ -n "$API_KEY" ]; then
  echo "→ Auth via ZIZKADB_API_KEY / SMOKE_API_KEY"
  AUTH_HEADER="Authorization: Bearer $API_KEY"
elif curl -sf -X POST "$BASE/v1/auth/dev-token" | grep -q 'access_token'; then
  echo "→ Dev token (local / ENV=development only)"
  AUTH_HEADER="Authorization: Bearer $DEV_KEY"
else
  echo "✗ No API key and /v1/auth/dev-token unavailable." >&2
  echo "  For staging/prod set: ZIZKADB_API_KEY='zizkadb_live_...'" >&2
  exit 1
fi

echo "→ Log event"
curl -sf -X POST "$BASE/v1/events" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d '{"agent":"support-bot","event":"smoke","data":{"ok":true}}' \
  | grep -q 'event_id'

if [ "${SKIP_SEARCH:-}" = "1" ]; then
  echo "→ Semantic search skipped (SKIP_SEARCH=1)"
elif [ -z "${OPENAI_API_KEY:-}" ] || [ "$OPENAI_API_KEY" = "sk-..." ] || [ "${#OPENAI_API_KEY}" -lt 20 ]; then
  echo "→ Semantic search skipped (set OPENAI_API_KEY)"
else
  echo "→ Semantic search"
  curl -sf -X POST "$BASE/v1/search" \
    -H "$AUTH_HEADER" \
    -H "Content-Type: application/json" \
    -d '{"agent":"support-bot","query":"test","limit":1}' \
    | grep -q '"results"'
fi

echo "✓ All smoke checks passed ($BASE)"

if [ "${SKIP_EXAMPLE:-}" != "1" ]; then
  echo ""
  bash "$ROOT/scripts/smoke-example.sh" "$BASE"
fi
