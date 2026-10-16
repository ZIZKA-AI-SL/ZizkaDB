#!/usr/bin/env bash
# Shared guards — source from deploy/backup/reset scripts (do not execute directly).

_zizka_root() {
  if [ -n "${ZIZKA_ROOT:-}" ]; then
    echo "$ZIZKA_ROOT"
    return
  fi
  # This file lives at infra/lib/ — repo root is two levels up (stable for all callers).
  echo "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
}

_zizka_load_env() {
  local root="$(_zizka_root)"
  if [ -f "$root/infra/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$root/infra/.env"
    set +a
  fi
}

# Production unless ENV is explicitly development (any case, outer spaces trimmed),
# matching the API (core/services/runtime_env.py). An unset ENV is production.
zizka_is_production() {
  _zizka_load_env
  local env_val
  env_val="$(printf '%s' "${ENV:-}" | tr '[:upper:]' '[:lower:]' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  [ "$env_val" != "development" ]
}

# Abort unless ENV=development — used before any destructive local-only command.
zizka_refuse_if_production() {
  if zizka_is_production; then
    echo "" >&2
    echo "════════════════════════════════════════════════════════" >&2
    echo "  REFUSED — production environment detected" >&2
    echo "════════════════════════════════════════════════════════" >&2
    echo "  infra/.env does not set ENV=development (unset counts as production)." >&2
    echo "  This command would destroy customer data." >&2
    echo "" >&2
    echo "  Safe production deploy:" >&2
    echo "    bash infra/deploy-production.sh" >&2
    echo "" >&2
    echo "  Backup only:" >&2
    echo "    bash infra/backup-postgres.sh" >&2
    echo "" >&2
    exit 1
  fi
}

# Require explicit confirmation before production deploy.
zizka_require_deploy_confirm() {
  if [ "${ZIZKA_DEPLOY_CONFIRM:-}" = "yes" ]; then
    return 0
  fi
  echo ""
  echo "Production deploy for db.zizka.ai"
  echo "  • Postgres backup runs first"
  echo "  • NEVER uses docker compose down -v"
  echo ""
  read -r -p "Type DEPLOY to continue: " ans
  if [ "$ans" != "DEPLOY" ]; then
    echo "Aborted."
    exit 1
  fi
}
