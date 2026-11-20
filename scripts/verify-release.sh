#!/usr/bin/env bash
# Pre-push verification: CLI, packages, syntax, dashboard build, optional API smoke.
set -euo pipefail

# Test runs are not installs: no telemetry ping unless asked for.
export ZIZKADB_TELEMETRY="${ZIZKADB_TELEMETRY:-false}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
FAIL=0

# Use the same Python as `zizkadb` CLI (avoids system/python vs pip mismatch)
if command -v zizkadb >/dev/null 2>&1; then
  PY="$(python3 -c "import sys; print(sys.executable)" 2>/dev/null || true)"
  ZDB_BIN="$(command -v zizkadb)"
  if [ -n "$ZDB_BIN" ] && [ -x "${ZDB_BIN%/bin/zizkadb}/bin/python" ]; then
    PY="${ZDB_BIN%/bin/zizkadb}/bin/python"
  elif [ -z "$PY" ] || ! "$PY" -c "import zizkadb" 2>/dev/null; then
    PY="$(command -v python3 || command -v python)"
  fi
else
  PY="$(command -v python3 || command -v python)"
fi

pass() { echo "✓ $1"; }
fail() { echo "✗ $1"; FAIL=1; }

echo "════════════════════════════════════════════════════════"
echo "  ZizkaDB pre-push verification"
echo "════════════════════════════════════════════════════════"
echo ""

echo "→ Python syntax (new code)"
while IFS= read -r -d '' f; do
  "$PY" -m py_compile "$f" || fail "syntax: $f"
done < <(find sdk/python/zizkadb/cli.py sdk/python/zizkadb/templates integrations examples -name '*.py' -print0 2>/dev/null)
pass "Python syntax"

echo "→ Install packages (editable)"
pip install -q -e sdk/python -e integrations/langchain -e integrations/crewai
pass "pip install -e"

echo "→ SDK unit tests"
"$PY" -c "
from zizkadb.client import ZizkaDB, DEFAULT_DEV_API_KEY
db = ZizkaDB(host='http://localhost:8000')
assert db._api_key == DEFAULT_DEV_API_KEY
assert db._headers()['Authorization'] == f'Bearer {DEFAULT_DEV_API_KEY}'
print('sdk unit ok')
"
pass "SDK unit tests"

echo "→ zizkadb init (all templates)"
TMP=$(mktemp -d)
for t in basic openai langchain crewai mcp-cursor; do
  zizkadb init "$TMP/$t" -t "$t" >/dev/null
  [ -f "$TMP/$t/README.md" ] || { fail "init template $t"; continue; }
  case $t in
    mcp-cursor)
      [ -f "$TMP/$t/mcp.json" ] || fail "mcp.json missing"
      [ -f "$TMP/$t/.env.example" ] || fail ".env.example missing for $t"
      ;;
    *)
      [ -f "$TMP/$t/agent.py" ] || fail "agent.py missing for $t"
      [ -f "$TMP/$t/.env.example" ] || fail ".env.example missing for $t"
      ;;
  esac
done
pass "zizkadb init all templates"
rm -rf "$TMP"

echo "→ Integration imports"
if ! "$PY" -c "
from zizkadb_langchain import ZizkaDBCallbackHandler
from zizkadb_crewai import ZizkaDBCrewLogger
print('imports ok')
" 2>/dev/null; then
  pip install -q langchain-core
  "$PY" -c "
from zizkadb_langchain import ZizkaDBCallbackHandler
from zizkadb_crewai import ZizkaDBCrewLogger
print('imports ok')
"
fi
pass "integration imports"

echo "→ Dashboard build"
(cd dashboard && npm ci -q && rm -rf .next && npm run build -q) >/dev/null
pass "dashboard build"

if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
  echo "→ API smoke (stack running)"
  bash scripts/smoke-test.sh
  ZIZKADB_HOST=http://localhost:8000 "$PY" scripts/demo-why.py
  pass "API smoke + demo-why.py"
else
  echo "○ API smoke skipped (no stack on :8000 — run: bash scripts/setup-local.sh)"
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "════════════════════════════════════════════════════════"
  echo "  All checks passed"
  echo "════════════════════════════════════════════════════════"
else
  echo "════════════════════════════════════════════════════════"
  echo "  Some checks FAILED"
  echo "════════════════════════════════════════════════════════"
  exit 1
fi
