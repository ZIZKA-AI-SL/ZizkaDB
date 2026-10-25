#!/usr/bin/env bash
# Restore a Postgres backup made by infra/backup-postgres.sh.
#
#   bash infra/restore-postgres.sh infra/backups/zizkadb_20261025T120000Z.sql.gz
#   bash infra/restore-postgres.sh <file> --yes      # no prompt (scripts, CI)
#
# Steps: backs up the current database first, stops the API and embed worker,
# recreates the database from the file, starts them again and checks event
# checksums. Qdrant vectors are not in the dump: with embeddings on, rebuild
# them with scripts/reindex-events.py, or restore infra/backup-qdrant.sh output.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FILE="${1:-}"
ASSUME_YES="${2:-}"
if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
  echo "Usage: bash infra/restore-postgres.sh <backup.sql.gz> [--yes]" >&2
  ls -1t infra/backups/zizkadb_*.sql.gz 2>/dev/null | head -5 | sed 's/^/  available: /' >&2 || true
  exit 1
fi
if ! gzip -t "$FILE" 2>/dev/null; then
  echo "ERROR: $FILE is not a valid gzip file." >&2
  exit 1
fi

COMPOSE=(docker compose -f infra/docker-compose.yml)
if [ -z "${ENV:-}" ] && ! grep -Eq '^[[:space:]]*(export[[:space:]]+)?ENV[[:space:]]*=' "$ROOT/infra/.env" 2>/dev/null; then
  export ENV=production
fi

# shellcheck disable=SC1091
source "$ROOT/infra/lib/production-guard.sh"
ZIZKA_ROOT="$ROOT"
_zizka_load_env

POSTGRES_USER="${POSTGRES_USER:-zizkadb}"
POSTGRES_DB="${POSTGRES_DB:-zizkadb}"

if ! "${COMPOSE[@]}" ps -q postgres 2>/dev/null | grep -q .; then
  echo "ERROR: Postgres container not running. Start the stack first." >&2
  exit 1
fi

echo "This replaces database '$POSTGRES_DB' with $FILE."
if [ "$ASSUME_YES" != "--yes" ]; then
  printf 'Type RESTORE to continue: '
  read -r answer
  [ "$answer" = "RESTORE" ] || { echo "Cancelled."; exit 1; }
fi

echo "→ Safety backup of the current database"
bash "$ROOT/infra/backup-postgres.sh"

psql_admin() {
  "${COMPOSE[@]}" exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres "$@"
}

echo "→ Stopping API and embed worker"
"${COMPOSE[@]}" stop api embed-worker >/dev/null 2>&1 || true

echo "→ Recreating database $POSTGRES_DB"
psql_admin -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\" WITH (FORCE)" >/dev/null
psql_admin -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\"" >/dev/null

echo "→ Loading $FILE"
gunzip -c "$FILE" | "${COMPOSE[@]}" exec -T postgres \
  psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null

EVENTS=$("${COMPOSE[@]}" exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT COUNT(*) FROM events" | tr -d '[:space:]')
echo "✓ Restored ($EVENTS events)"

echo "→ Starting API and embed worker"
"${COMPOSE[@]}" up -d --no-deps api >/dev/null
"${COMPOSE[@]}" up -d --no-deps embed-worker >/dev/null 2>&1 || true

for _ in $(seq 1 60); do
  if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "✓ API healthy"
    echo "  Check integrity: GET /v1/events/verify?agent=<agent> with your API key."
    exit 0
  fi
  sleep 2
done
echo "WARN: API did not report healthy within 2 minutes; check: ${COMPOSE[*]} logs api" >&2
exit 1
