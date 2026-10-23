#!/usr/bin/env bash
# Publish zizkadb-sdk (PyPI + npm) and zizkadb-mcp (PyPI).
#
# PyPI 403 Forbidden usually means:
#   - Token is from a PyPI account that does NOT own zizkadb-sdk / zizkadb-mcp
#   - TWINE_USERNAME is not exactly __token__
#   - Token scope is "single project" but wrong project name
#   - Token expired or copied with extra whitespace/newlines
#
# Fix: https://pypi.org/manage/account/token/
#   - Use account that published 0.2.1 / 0.1.1 originally
#   - Scope: "Entire account" (or both zizkadb-sdk + zizkadb-mcp)
#   - export TWINE_USERNAME=__token__
#   - export TWINE_PASSWORD=pypi-AgEIcHlwaS5vcmcC...   # full token, no quotes
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SDK_VER="$(grep '^version' sdk/python/pyproject.toml | head -1 | sed 's/.*"\(.*\)".*/\1/')"
MCP_VER="$(grep '^version' mcp/pyproject.toml | head -1 | sed 's/.*"\(.*\)".*/\1/')"

if [[ "${TWINE_USERNAME:-}" != "__token__" ]]; then
  echo "Set PyPI credentials before running:"
  echo '  export TWINE_USERNAME=__token__'
  echo '  export TWINE_PASSWORD=pypi-...'
  exit 1
fi

if [[ -z "${TWINE_PASSWORD:-}" ]]; then
  echo "TWINE_PASSWORD is empty."
  exit 1
fi

echo "→ Build Python SDK ${SDK_VER}"
pip install -q build twine
(cd sdk/python && rm -rf dist build && python3 -m build)

echo "→ Verify PyPI wheel matches source (LangChain callbacks)"
python3 - <<'PY'
import glob, zipfile, sys
from pathlib import Path
wheels = glob.glob("sdk/python/dist/zizkadb_sdk-*.whl")
if not wheels:
    sys.exit("No wheel built")
with zipfile.ZipFile(wheels[0]) as z:
    whl = next(n for n in z.namelist() if n.endswith("langchain/callbacks.py"))
    pkg = z.read(whl).decode()
for marker in ("on_llm_error", "on_chain_start", "_RUN_PARENT_MAX"):
    if marker not in pkg:
        print(f"✗ PyPI wheel missing {marker}")
        sys.exit(1)
print("✓ PyPI wheel callbacks include required handlers")
PY

echo "→ Build MCP ${MCP_VER}"
(cd mcp && rm -rf dist build && python3 -m build)

echo "→ Build TypeScript SDK"
(cd sdk/typescript && npm ci -q && npm run build)

# --skip-existing: MCP versions independently of the SDK, so an unchanged
# MCP version is already on PyPI; it also makes a re-run after a failed npm
# step safe.
echo "→ Upload zizkadb-sdk ${SDK_VER} to PyPI"
twine upload --skip-existing --verbose "sdk/python/dist/zizkadb_sdk-${SDK_VER}"*

echo "→ Upload zizkadb-mcp ${MCP_VER} to PyPI (skipped if ${MCP_VER} is already published)"
twine upload --skip-existing --verbose "mcp/dist/zizkadb_mcp-${MCP_VER}"*

echo "→ Publish to npm (run: npm login --auth-type=web  if not logged in)"
(cd sdk/typescript && npm publish --access public)

echo ""
echo "Verify (run each command separately):"
echo "  pip index versions zizkadb-sdk"
echo "  pip index versions zizkadb-mcp"
echo "  npm view zizkadb-sdk version"
