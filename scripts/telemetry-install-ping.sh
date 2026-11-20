#!/usr/bin/env bash
# Anonymous OSS install ping — fire-and-forget after Docker stack is healthy.
# Counts self-hosted Docker adoption separately from PyPI download stats.
#
# Usage:
#   bash scripts/telemetry-install-ping.sh [sdk] [sdk_version]
# Defaults: sdk=docker, sdk_version=oss
#
# Opt out: export ZIZKADB_TELEMETRY=false
set -euo pipefail

TELEMETRY_URL="${ZIZKADB_TELEMETRY_URL:-https://db.zizka.ai/v1/telemetry}"
SDK="${1:-docker}"
SDK_VERSION="${2:-oss}"

case "${ZIZKADB_TELEMETRY:-}" in
  false|0|no|off|FALSE|NO|OFF) exit 0 ;;
esac

INSTALL_DIR="${HOME}/.zizkadb"
INSTALL_ID_FILE="${INSTALL_DIR}/install_id"
mkdir -p "$INSTALL_DIR"

if [ -f "$INSTALL_ID_FILE" ] && [ -s "$INSTALL_ID_FILE" ]; then
  INSTALL_ID="$(tr -d '[:space:]' < "$INSTALL_ID_FILE")"
else
  if command -v uuidgen >/dev/null 2>&1; then
    INSTALL_ID="$(uuidgen | tr '[:upper:]' '[:lower:]')"
  elif command -v python3 >/dev/null 2>&1; then
    INSTALL_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
  else
    INSTALL_ID="$(date +%s)-$$"
  fi
  printf '%s' "$INSTALL_ID" > "$INSTALL_ID_FILE"
fi

OS="$(uname -s 2>/dev/null || echo unknown)"

# Where this runs, so CI runs are not counted as installs (same CI variables
# as the SDKs).
ENV_KIND=machine
for v in CI CONTINUOUS_INTEGRATION GITHUB_ACTIONS GITLAB_CI CIRCLECI TRAVIS BUILDKITE \
         JENKINS_URL TF_BUILD TEAMCITY_VERSION BITBUCKET_BUILD_NUMBER CODEBUILD_BUILD_ID \
         DRONE APPVEYOR SEMAPHORE; do
  val="$(printenv "$v" 2>/dev/null || true)"
  case "$val" in
    ""|false|0|no|off|FALSE|NO|OFF) ;;
    *) ENV_KIND=ci; break ;;
  esac
done
if [ "$ENV_KIND" = machine ] && { [ -f /.dockerenv ] || [ -f /run/.containerenv ] || [ -n "${KUBERNETES_SERVICE_HOST:-}" ]; }; then
  ENV_KIND=container
fi

PAYLOAD="$(printf '{"install_id":"%s","sdk":"%s","sdk_version":"%s","python":null,"os":"%s","mode":"self-hosted","env":"%s"}' \
  "$INSTALL_ID" "$SDK" "$SDK_VERSION" "$OS" "$ENV_KIND")"

curl -sf -X POST "$TELEMETRY_URL" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  --max-time 3 \
  >/dev/null 2>&1 || true
