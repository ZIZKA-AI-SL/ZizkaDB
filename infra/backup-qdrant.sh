#!/usr/bin/env bash
# Backup Qdrant vector storage — Docker volume or native .local/qdrant/storage.
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
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$BACKUP_DIR"

# Docker path: snapshot via Qdrant API when container is running
if "${COMPOSE[@]}" ps -q qdrant 2>/dev/null | grep -q .; then
  QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
  OUT="$BACKUP_DIR/qdrant_docker_${STAMP}.snapshot"
  echo "→ Creating Qdrant snapshot via API ($QDRANT_URL)"
  SNAPSHOT=$(curl -sf -X POST "$QDRANT_URL/collections/agent_events/snapshots" | \
    python3 -c "import sys,json; print(json.load(sys.stdin).get('result',{}).get('name',''))")
  if [ -z "$SNAPSHOT" ]; then
    echo "ERROR: Qdrant snapshot creation failed" >&2
    exit 1
  fi
  curl -sf "$QDRANT_URL/collections/agent_events/snapshots/$SNAPSHOT" -o "$OUT"
  SIZE=$(du -h "$OUT" | cut -f1)
  echo "✓ Docker Qdrant snapshot saved ($SIZE) → $OUT"
else
  # Native path: tar local storage directory
  STORAGE="$ROOT/.local/qdrant/storage"
  if [ ! -d "$STORAGE" ]; then
    echo "ERROR: Qdrant storage not found at $STORAGE and Docker qdrant not running." >&2
    exit 1
  fi
  OUT="$BACKUP_DIR/qdrant_native_${STAMP}.tar.gz"
  echo "→ Archiving native Qdrant storage → $OUT"
  tar -czf "$OUT" -C "$ROOT/.local/qdrant" storage
  SIZE=$(du -h "$OUT" | cut -f1)
  echo "✓ Native Qdrant backup saved ($SIZE)"
fi

find "$BACKUP_DIR" -name 'qdrant_*.tar.gz' -mtime +"$KEEP_DAYS" -delete 2>/dev/null || true
find "$BACKUP_DIR" -name 'qdrant_docker_*' -mtime +"$KEEP_DAYS" -delete 2>/dev/null || true
echo "  Kept backups in $BACKUP_DIR (last ${KEEP_DAYS} days)"
