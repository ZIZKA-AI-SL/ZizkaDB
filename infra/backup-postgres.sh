#!/usr/bin/env bash
# Dump Postgres to infra/backups/ — run before every production deploy and on a daily cron.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose -f infra/docker-compose.yml)

# infra/docker-compose.yml requires ENV just to parse; ps/exec never change the
# running containers, so supply production when infra/.env does not set it.
if [ -z "${ENV:-}" ] && ! grep -Eq '^[[:space:]]*(export[[:space:]]+)?ENV[[:space:]]*=' "$ROOT/infra/.env" 2>/dev/null; then
  export ENV=production
fi
BACKUP_DIR="$ROOT/infra/backups"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"

# shellcheck disable=SC1091
source "$ROOT/infra/lib/production-guard.sh"
ZIZKA_ROOT="$ROOT"
_zizka_load_env

POSTGRES_USER="${POSTGRES_USER:-zizkadb}"
POSTGRES_DB="${POSTGRES_DB:-zizkadb}"

if [ ! -f "$ROOT/infra/.env" ]; then
  echo "WARN: infra/.env not found — using defaults ($POSTGRES_USER / $POSTGRES_DB)" >&2
fi
CONTAINER="${POSTGRES_CONTAINER:-zizkadb_postgres}"

mkdir -p "$BACKUP_DIR"

if ! "${COMPOSE[@]}" ps -q postgres 2>/dev/null | grep -q .; then
  echo "ERROR: Postgres container not running. Start stack first." >&2
  exit 1
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/zizkadb_${STAMP}.sql.gz"

echo "→ Backing up Postgres → $OUT"
"${COMPOSE[@]}" exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-acl \
  | gzip -9 > "$OUT"

USER_COUNT=$("${COMPOSE[@]}" exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT COUNT(*) FROM users;" 2>/dev/null | tr -d '[:space:]' || echo "?")

SIZE=$(du -h "$OUT" | cut -f1)
echo "✓ Backup complete ($SIZE, users=$USER_COUNT)"

# Retention
find "$BACKUP_DIR" -name 'zizkadb_*.sql.gz' -mtime +"$KEEP_DAYS" -delete 2>/dev/null || true
echo "  Kept backups in $BACKUP_DIR (last ${KEEP_DAYS} days)"
