#!/usr/bin/env bash
# Entrypoint for the single-container image: runs Postgres, Redis, the API and
# the dashboard, and exits (stopping the container) if any of them dies.
# All state lives in /data.
set -euo pipefail

DATA=/data
PGDATA="$DATA/postgres"
SECRETS="$DATA/secrets.env"
SCHEMA_MARKER="$PGDATA/.zizkadb_schema_applied"
DEMO_MARKER="$DATA/.demo_seeded"

log() { echo "[zizkadb] $*"; }
# setpriv keeps the environment, so give each user a HOME it can read
# (asyncpg and psql look in ~/.postgresql).
PG_USER=(env HOME=/var/lib/postgresql setpriv --reuid=postgres --regid=postgres --init-groups)
APP_USER=(env HOME=/tmp setpriv --reuid=zizkadb --regid=zizkadb --init-groups)
as_pg() { "${PG_USER[@]}" "$@"; }
as_app() { "${APP_USER[@]}" "$@"; }

mkdir -p "$PGDATA" "$DATA/redis" "$DATA/community_uploads" /var/run/postgresql
chown postgres:postgres "$PGDATA" /var/run/postgresql
chmod 700 "$PGDATA"
chown zizkadb:zizkadb "$DATA/redis" "$DATA/community_uploads"

# ── Secrets: generated once per install, kept in the volume ──────────────────
if [ ! -f "$SECRETS" ]; then
  (
    umask 077
    python - > "$SECRETS" <<'PY'
import secrets
print(f"GEN_JWT_SECRET={secrets.token_hex(32)}")
print(f"GEN_JWT_REFRESH_SECRET={secrets.token_hex(32)}")
print(f"GEN_DEV_API_KEY=zizkadb_local_{secrets.token_hex(24)}")
PY
  )
fi
# shellcheck disable=SC1090
. "$SECRETS"
export JWT_SECRET="${JWT_SECRET:-$GEN_JWT_SECRET}"
export JWT_REFRESH_SECRET="${JWT_REFRESH_SECRET:-$GEN_JWT_REFRESH_SECRET}"
# The API refuses ENV=production with the well-known dev key.
if [ "${ENV:-development}" = "production" ] && [ "${DEV_API_KEY:-}" = "zizkadb_dev_local" ]; then
  export DEV_API_KEY="$GEN_DEV_API_KEY"
fi
unset GEN_JWT_SECRET GEN_JWT_REFRESH_SECRET GEN_DEV_API_KEY

# ── Postgres: create the cluster and schema on first start ───────────────────
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  log "First start: creating the database (about 10 seconds)..."
  as_pg initdb -D "$PGDATA" -U postgres -E UTF8 \
    --auth-local=trust --auth-host=scram-sha-256 >/dev/null
fi

if [ ! -f "$SCHEMA_MARKER" ]; then
  as_pg pg_ctl -D "$PGDATA" -o "-c listen_addresses=''" -w start >/dev/null
  as_pg psql -q -v ON_ERROR_STOP=1 -U postgres -d postgres <<'SQL'
SELECT 'CREATE ROLE zizkadb LOGIN SUPERUSER PASSWORD ''zizkadb'''
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zizkadb') \gexec
SELECT 'CREATE DATABASE zizkadb OWNER zizkadb'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'zizkadb') \gexec
SQL
  # One transaction: a failed schema load leaves nothing behind to retry over.
  as_pg psql -q -1 -v ON_ERROR_STOP=1 -U zizkadb -d zizkadb -f /opt/zizkadb/schema.sql >/dev/null
  as_pg touch "$SCHEMA_MARKER"
  as_pg pg_ctl -D "$PGDATA" -m fast -w stop >/dev/null
fi

# ── Run everything ───────────────────────────────────────────────────────────
PIDS=()

shutdown() {
  trap - TERM INT
  log "Stopping..."
  for pid in "${PIDS[@]}" "${BANNER_PID:-}"; do
    [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null || true
  done
  # Fast shutdown: disconnect clients and flush, without waiting on them.
  as_pg pg_ctl -D "$PGDATA" -m fast -w stop >/dev/null 2>&1 || true
  wait || true
}
trap 'shutdown; exit 0' TERM INT

# Started with exec (not through the as_* functions) so $! is the service
# itself and signals reach it.
( exec "${PG_USER[@]}" postgres -D "$PGDATA" \
    -c listen_addresses=127.0.0.1 \
    -c jit=off \
    -c unix_socket_directories=/var/run/postgresql ) &
PG_PID=$!

( exec "${APP_USER[@]}" redis-server --bind 127.0.0.1 --port 6379 --dir "$DATA/redis" \
    --appendonly yes --daemonize no --logfile "" >/dev/null ) &
PIDS+=($!)

for _ in $(seq 1 60); do
  pg_isready -q -h 127.0.0.1 -U zizkadb -d zizkadb && break
  sleep 0.5
done

log "Starting API and dashboard..."
( cd /app/api && exec "${APP_USER[@]}" \
    uvicorn main:app --host 0.0.0.0 --port 8000 --log-level warning ) &
PIDS+=($!)

( cd /app/dashboard && PORT=3001 HOSTNAME=0.0.0.0 NODE_ENV=production \
    exec "${APP_USER[@]}" node server.js >/dev/null ) &
PIDS+=($!)

# Seed the demo and print the banner without blocking signal handling.
(
  ready=0
  for _ in $(seq 1 240); do
    if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 \
       && curl -fsS -o /dev/null http://127.0.0.1:3001/login 2>/dev/null; then
      ready=1
      break
    fi
    sleep 0.5
  done
  if [ "$ready" -ne 1 ]; then
    log "Not ready after 2 minutes — see the errors above."
    exit 0
  fi

  if [ "${ENV:-development}" = "development" ] \
     && [ "${ZIZKADB_SEED_DEMO:-true}" != "false" ] && [ ! -f "$DEMO_MARKER" ]; then
    if as_app python /opt/zizkadb/seed_demo.py > "$DATA/demo-chain.txt" 2>"$DATA/demo-seed.err"; then
      touch "$DEMO_MARKER"
    else
      log "Demo seed skipped: $(tail -n 1 "$DATA/demo-seed.err" 2>/dev/null || true)"
    fi
  fi

  echo ""
  echo "════════════════════════════════════════════════════════"
  echo "  ZizkaDB is ready"
  echo "════════════════════════════════════════════════════════"
  if [ -s "$DATA/demo-chain.txt" ]; then
    echo ""
    echo "  Why did the demo agent call lookup_order?"
    echo ""
    sed 's/^/    /' "$DATA/demo-chain.txt"
  fi
  echo ""
  if [ "${ENV:-development}" = "development" ]; then
    echo "  Dashboard:  http://localhost:3001/dashboard/activity?agent=support-bot"
    echo "  API:        http://localhost:8000   (key: ${DEV_API_KEY})"
  else
    echo "  Dashboard:  http://localhost:3001   (sign in with SELFHOST_ADMIN_TOKEN)"
    echo "  API:        http://localhost:8000"
  fi
  echo "  Swagger:    http://localhost:8000/swagger"
  echo ""
) &
BANNER_PID=$!

# Exit as soon as any service dies so Docker's restart policy can act on it.
set +e
wait -n "$PG_PID" "${PIDS[@]}"
code=$?
set -e
log "A service exited (status $code); shutting down."
shutdown
exit "$code"
