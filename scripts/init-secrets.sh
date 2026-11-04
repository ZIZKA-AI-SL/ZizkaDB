#!/usr/bin/env bash
# Replace the well-known values from .env.example with random secrets, so a new
# install never runs with a default password. Prints key names, never values.
#
#   bash scripts/init-secrets.sh              # infra/.env
#   bash scripts/init-secrets.sh path/to/.env
#
# Safe to run again: only empty or default values are replaced.
# - POSTGRES_PASSWORD (and the matching DATABASE_URL) only when Docker is
#   available and no ZizkaDB database volume exists yet: Postgres keeps the
#   password it was created with, so changing it later would lock the API out.
# - REDIS_PASSWORD and QDRANT_API_KEY when empty. Neither is stored in the data,
#   so adding them to an existing install is fine.
# - JWT_SECRET and JWT_REFRESH_SECRET while they still hold the placeholder.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${1:-infra/.env}"
if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: $ENV_FILE not found (copy .env.example first)." >&2
  exit 1
fi

rand_hex() { head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }
current() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }

set_value() {
  local tmp
  tmp="$(mktemp)"
  if grep -qE "^$1=" "$ENV_FILE"; then
    awk -v k="$1=" -v v="$2" 'index($0, k) == 1 { print k v; next } { print }' "$ENV_FILE" > "$tmp"
  else
    cat "$ENV_FILE" > "$tmp"
    printf '%s=%s\n' "$1" "$2" >> "$tmp"
  fi
  # cat, not mv: keeps the file's owner and permissions.
  cat "$tmp" > "$ENV_FILE"
  rm -f "$tmp"
  CHANGED="$CHANGED $1"
}

pg_volume_exists() {
  docker volume ls -q 2>/dev/null | grep -q 'postgres_data$'
}

CHANGED=""

pg_pw="$(current POSTGRES_PASSWORD)"
if [ -z "$pg_pw" ] || [ "$pg_pw" = "zizkadb" ]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "  Postgres password left as is (no Docker: your own Postgres sets it)."
  elif pg_volume_exists; then
    echo "  Postgres password left as is: a database volume already exists and keeps its password."
  else
    new_pw="$(rand_hex 24)"
    set_value POSTGRES_PASSWORD "$new_pw"
    db_url="$(current DATABASE_URL)"
    case "$db_url" in
      *"zizkadb:zizkadb@"*) set_value DATABASE_URL "${db_url/zizkadb:zizkadb@/zizkadb:${new_pw}@}" ;;
    esac
  fi
fi

[ -n "$(current REDIS_PASSWORD)" ] || set_value REDIS_PASSWORD "$(rand_hex 24)"
[ -n "$(current QDRANT_API_KEY)" ] || set_value QDRANT_API_KEY "$(rand_hex 24)"

for key in JWT_SECRET JWT_REFRESH_SECRET; do
  case "$(current "$key")" in
    ""|change-this*) set_value "$key" "$(rand_hex 32)" ;;
  esac
done

chmod 600 "$ENV_FILE" 2>/dev/null || true

if [ -n "$CHANGED" ]; then
  echo "✓ Generated secrets in $ENV_FILE:$CHANGED"
else
  echo "✓ Secrets in $ENV_FILE already set"
fi
