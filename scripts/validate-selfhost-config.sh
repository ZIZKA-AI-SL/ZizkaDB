#!/usr/bin/env bash
# Validate self-host configuration and connectivity.
# Usage: bash scripts/validate-selfhost-config.sh [--production]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PRODUCTION_MODE=0
if [ "${1:-}" = "--production" ]; then
  PRODUCTION_MODE=1
fi

ERRORS=0
WARNS=0

fail() {
  echo "ERROR: $1" >&2
  ERRORS=$((ERRORS + 1))
}

warn() {
  echo "WARN: $1" >&2
  WARNS=$((WARNS + 1))
}

ok() {
  echo "✓ $1"
}

if [ ! -f infra/.env ]; then
  fail "infra/.env missing — run: cp .env.example infra/.env"
else
  set -a
  # shellcheck disable=SC1091
  source infra/.env
  set +a
  ok "infra/.env loaded"
fi

# Rosetta check (macOS)
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "x86_64" ]; then
  if [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ]; then
    warn "Rosetta shell — Docker may fail; use native arm64 Terminal or restart-native-stack.sh"
  fi
fi

# Docker availability
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  ok "Docker daemon running"
else
  warn "Docker not available — use bash scripts/restart-native-stack.sh"
fi

# Required vars
for var in DATABASE_URL REDIS_URL QDRANT_URL JWT_SECRET JWT_REFRESH_SECRET; do
  if [ -z "${!var:-}" ]; then
    fail "$var is not set"
  else
    ok "$var is set"
  fi
done

# OpenAI key
if [ -z "${OPENAI_API_KEY:-}" ] || [ "$OPENAI_API_KEY" = "sk-..." ] || [ "${#OPENAI_API_KEY}" -lt 20 ]; then
  warn "OPENAI_API_KEY missing or placeholder — search/embeddings will fail"
else
  ok "OPENAI_API_KEY looks configured"
fi

# JWT placeholders (must match core/main.py _DEFAULT_JWT_SECRETS minus empty string)
_JWT_PLACEHOLDERS=(
  "dev-secret"
  "dev-refresh-secret"
  "dev-secret-change-in-production"
  "dev-refresh-secret-change-in-production"
  "change-this-to-a-random-32-char-string"
  "change-this-to-another-random-32-char-string"
)

_jwt_is_placeholder() {
  local val="${1:-}"
  local p
  for p in "${_JWT_PLACEHOLDERS[@]}"; do
    if [ "$val" = "$p" ]; then
      return 0
    fi
  done
  return 1
}

_check_jwt_var() {
  local var_name="$1"
  local val="${!var_name:-}"
  # Unset is already reported by the required-var loop above.
  if [ -z "$val" ]; then
    return
  fi
  if _jwt_is_placeholder "$val"; then
    if [ "$PRODUCTION_MODE" -eq 1 ]; then
      fail "$var_name is still a published placeholder (API refuses to start in production)"
    else
      warn "$var_name is a default placeholder (ok for local dev)"
    fi
  fi
}

_check_jwt_var JWT_SECRET
_check_jwt_var JWT_REFRESH_SECRET

# Environment mode
ENV_VAL="$(echo "${ENV:-development}" | tr '[:upper:]' '[:lower:]' | xargs)"
if [ "$PRODUCTION_MODE" -eq 1 ]; then
  if [ "$ENV_VAL" != "production" ]; then
    fail "ENV must be production (currently: $ENV_VAL)"
  else
    ok "ENV=production"
  fi
  # The API refuses to boot in production with an empty or default DEV_API_KEY
  # (compose substitutes the default when unset), and never accepts it as auth.
  case "${DEV_API_KEY:-}" in
    ""|zizkadb_dev_local|agdb_dev_local)
      fail "DEV_API_KEY must be a unique random value in production (the API refuses to start otherwise)" ;;
    *)
      ok "DEV_API_KEY is a unique value (not accepted as auth in production)" ;;
  esac
  if [ "${DEPLOYMENT_MODE:-managed}" != "self_hosted" ]; then
    warn "DEPLOYMENT_MODE is not 'self_hosted' — plan-based entitlement checks (e.g. API key limits) will not apply the Self-Hosted plan"
    # Not self-hosted: the dashboard signs in with email OTP.
    for var in EMAIL_HOST EMAIL_USER EMAIL_PASS; do
      if [ -z "${!var:-}" ]; then
        fail "$var required for OTP login in production mode validation"
      fi
    done
  else
    ok "DEPLOYMENT_MODE=self_hosted"
    # Self-hosted dashboard signs in with the admin token — no email needed.
    if [ -z "${SELFHOST_ADMIN_TOKEN:-}" ]; then
      fail "SELFHOST_ADMIN_TOKEN required — without it dashboard login is disabled in production"
    elif [ "${#SELFHOST_ADMIN_TOKEN}" -lt 16 ]; then
      fail "SELFHOST_ADMIN_TOKEN is shorter than 16 chars — the API refuses to start. Use: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
    elif [ "${#SELFHOST_ADMIN_TOKEN}" -lt 24 ]; then
      warn "SELFHOST_ADMIN_TOKEN is short (<24 chars) — use: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
    else
      ok "SELFHOST_ADMIN_TOKEN set"
    fi
  fi
else
  if [ "$ENV_VAL" = "production" ]; then
    warn "ENV=production in infra/.env — dev key bypass disabled"
  fi
  if [ -n "${DEV_API_KEY:-}" ]; then
    ok "DEV_API_KEY set for local dev"
  else
    warn "DEV_API_KEY not set — SDK may need ZIZKADB_API_KEY on localhost"
  fi
fi

# Port connectivity
check_port() {
  local name="$1"
  local url="$2"
  if curl -sf --connect-timeout 2 --max-time 5 "$url" >/dev/null 2>&1; then
    ok "$name reachable ($url)"
  else
    warn "$name not reachable ($url) — start stack first"
  fi
}

check_port "API" "http://localhost:8000/health"
check_port "Deep health" "http://localhost:8000/health/deep"
check_port "Qdrant" "http://localhost:6333/"
check_port "Dashboard" "http://127.0.0.1:3001/login"

# Python SDK
if [ -x .venv/bin/python ] && .venv/bin/python -c "import zizkadb" 2>/dev/null; then
  ok "Python SDK installed in .venv"
else
  warn "Python SDK not installed — run: bash scripts/bootstrap-local.sh"
fi

echo ""
if [ "$ERRORS" -gt 0 ]; then
  echo "Validation failed: $ERRORS error(s), $WARNS warning(s)"
  exit 1
fi

echo "Validation passed: $WARNS warning(s)"
exit 0
