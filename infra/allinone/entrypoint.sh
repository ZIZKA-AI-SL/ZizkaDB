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
# Same rule as the API (services/runtime_env.py): trim the ends, ignore case.
is_dev() {
  local v
  v="$(printf '%s' "${ENV:-production}" | tr '[:upper:]' '[:lower:]')"
  v="${v#"${v%%[![:space:]]*}"}"
  v="${v%"${v##*[![:space:]]}"}"
  [ "$v" = "development" ]
}
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
# Keys that are missing or empty are added; existing values are never replaced,
# so a secrets.env from an older image is upgraded in place. The file is
# rewritten atomically, so an interrupted first start cannot leave a half line.
(
  umask 077
  python - "$SECRETS" <<'PY'
import os
import secrets
import sys

path = sys.argv[1]
lines, have = [], set()
if os.path.exists(path):
    with open(path) as f:
        for line in f:
            key, sep, value = line.rstrip("\n").partition("=")
            if sep and value:
                lines.append(f"{key}={value}")
                have.add(key)
wanted = {
    "GEN_JWT_SECRET": lambda: secrets.token_hex(32),
    "GEN_JWT_REFRESH_SECRET": lambda: secrets.token_hex(32),
    "GEN_SELFHOST_ADMIN_TOKEN": lambda: secrets.token_urlsafe(32),
    "GEN_PG_PASSWORD": lambda: secrets.token_hex(24),
}
missing = [k for k in wanted if k not in have]
if missing:
    lines += [f"{k}={wanted[k]()}" for k in missing]
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
PY
)
# shellcheck disable=SC1090
. "$SECRETS"
export JWT_SECRET="${JWT_SECRET:-$GEN_JWT_SECRET}"
export JWT_REFRESH_SECRET="${JWT_REFRESH_SECRET:-$GEN_JWT_REFRESH_SECRET}"
# An operator token wins unless it is blank (the API trims it the same way).
ADMIN_TOKEN_FROM_ENV=false
if [ -n "$(printf '%s' "${SELFHOST_ADMIN_TOKEN:-}" | tr -d '[:space:]')" ]; then
  ADMIN_TOKEN_FROM_ENV=true
else
  SELFHOST_ADMIN_TOKEN="$GEN_SELFHOST_ADMIN_TOKEN"
fi
export SELFHOST_ADMIN_TOKEN
# The local role's password: the generated one, or the one in an
# operator-supplied DATABASE_URL that points at this container's database.
PG_PASSWORD="$GEN_PG_PASSWORD"
if [ -n "${DATABASE_URL:-}" ]; then
  url_pw="$(python - <<'PY'
import os
from urllib.parse import unquote, urlsplit

u = urlsplit(os.environ["DATABASE_URL"])
if u.hostname in ("127.0.0.1", "localhost", "::1") and u.username == "zizkadb" and u.password:
    print(unquote(u.password), end="")
PY
)"
  [ -n "$url_pw" ] && PG_PASSWORD="$url_pw"
  unset url_pw
fi
export DATABASE_URL="${DATABASE_URL:-postgresql://zizkadb:${PG_PASSWORD}@127.0.0.1:5432/zizkadb}"
if is_dev; then
  # Local one-click mode keeps the well-known key the README and SDK examples use.
  export DEV_API_KEY="${DEV_API_KEY:-zizkadb_dev_local}"
elif [ -n "${DEV_API_KEY:-}" ]; then
  # Production never accepts dev keys, and the API refuses to start with a
  # well-known one. Drop it with a warning instead of a crash loop.
  log "Ignoring DEV_API_KEY: dev keys work only with -e ENV=development."
  unset DEV_API_KEY
fi
unset GEN_JWT_SECRET GEN_JWT_REFRESH_SECRET GEN_DEV_API_KEY GEN_SELFHOST_ADMIN_TOKEN GEN_PG_PASSWORD

# Public address (optional): the base for links the API generates. The front
# proxy serves the dashboard and the API (/v1) on one port, so that one
# address is enough.
FRONT_PORT="${FRONT_PORT:-3001}"
export FRONT_PORT
DASHBOARD_ADDRESS="http://localhost:${FRONT_PORT}"
[ -n "${ZIZKADB_PUBLIC_URL:-}" ] && DASHBOARD_ADDRESS="${ZIZKADB_PUBLIC_URL%/}"
if is_dev; then
  # Local mode publishes the API on :8000, where the SDKs look by default.
  export PUBLIC_API_URL="${PUBLIC_API_URL:-http://localhost:8000}"
else
  export PUBLIC_API_URL="${PUBLIC_API_URL:-$DASHBOARD_ADDRESS}"
  # The dashboard calls the API on its own origin, so production accepts
  # browser calls from that origin only. Local mode keeps the API's default.
  export CORS_ALLOWED_ORIGINS="${CORS_ALLOWED_ORIGINS:-$DASHBOARD_ADDRESS}"
fi

# ── Postgres: create the cluster and schema on first start ───────────────────
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  log "First start: creating the database (about 10 seconds)..."
  as_pg initdb -D "$PGDATA" -U postgres -E UTF8 \
    --auth-local=trust --auth-host=scram-sha-256 >/dev/null
fi

# The local role's password follows secrets.env. The marker holds a hash of the
# password last set, so a changed or regenerated secrets.env is applied on the
# next start instead of locking the API out. Installs from older images used a
# fixed password; it is replaced here the first time this image starts.
PASSWORD_MARKER="$PGDATA/.zizkadb_password_sha256"
PG_PASSWORD_HASH="$(printf '%s' "$PG_PASSWORD" | sha256sum | cut -d' ' -f1)"
if [ -f "$SCHEMA_MARKER" ] && [ ! -f "$PASSWORD_MARKER" ] && ! is_dev; then
  # First start of this image on a volume from an older one, which ran in
  # development mode unless told otherwise.
  log "Upgraded volume: this image runs in production mode by default."
  log "  The well-known dev key and one-click sign-in are off; sign in with the admin token."
  log "  For local one-click mode, start the container with -e ENV=development."
fi
if [ ! -f "$SCHEMA_MARKER" ] || [ "$(cat "$PASSWORD_MARKER" 2>/dev/null)" != "$PG_PASSWORD_HASH" ]; then
  as_pg pg_ctl -D "$PGDATA" -o "-c listen_addresses=''" -w start >/dev/null
  # psql reads the password verbatim from its environment (\getenv), never
  # from argv, and :'pw' quotes it, so any character is safe.
  PG_ROLE_PASSWORD="$PG_PASSWORD" as_pg psql -q -v ON_ERROR_STOP=1 -U postgres -d postgres >/dev/null <<'SQL'
\getenv pw PG_ROLE_PASSWORD
SELECT format('CREATE ROLE zizkadb LOGIN SUPERUSER PASSWORD %L', :'pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zizkadb') \gexec
ALTER ROLE zizkadb PASSWORD :'pw';
SQL
  printf '%s\n' "$PG_PASSWORD_HASH" | as_pg tee "$PASSWORD_MARKER" >/dev/null
  if [ ! -f "$SCHEMA_MARKER" ]; then
    as_pg psql -q -v ON_ERROR_STOP=1 -U postgres -d postgres <<'SQL'
SELECT 'CREATE DATABASE zizkadb OWNER zizkadb'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'zizkadb') \gexec
SQL
    # One transaction: a failed schema load leaves nothing behind to retry over.
    as_pg psql -q -1 -v ON_ERROR_STOP=1 -U zizkadb -d zizkadb -f /opt/zizkadb/schema.sql >/dev/null
    as_pg touch "$SCHEMA_MARKER"
  fi
  as_pg pg_ctl -D "$PGDATA" -m fast -w stop >/dev/null
fi
unset PG_PASSWORD PG_PASSWORD_HASH

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
    uvicorn main:app --host 0.0.0.0 --port "${API_INTERNAL_PORT:-8000}" --log-level warning ) &
PIDS+=($!)

# The dashboard listens inside the container only; the front proxy owns the
# public port 3001 and routes /v1, /health and /swagger to the API.
# front-proxy.js reads the same API_INTERNAL_PORT / DASHBOARD_INTERNAL_PORT.
( cd /app/dashboard && PORT="${DASHBOARD_INTERNAL_PORT:-3002}" HOSTNAME=127.0.0.1 NODE_ENV=production \
    exec "${APP_USER[@]}" node server.js >/dev/null ) &
PIDS+=($!)

( exec "${APP_USER[@]}" node /opt/zizkadb/front-proxy.js ) &
PIDS+=($!)

# Seed the demo and print the banner without blocking signal handling.
(
  ready=0
  for _ in $(seq 1 240); do
    if curl -fsS "http://127.0.0.1:${API_INTERNAL_PORT:-8000}/health" >/dev/null 2>&1 \
       && curl -fsS -o /dev/null "http://127.0.0.1:${FRONT_PORT}/login" 2>/dev/null; then
      ready=1
      break
    fi
    sleep 0.5
  done
  if [ "$ready" -ne 1 ]; then
    log "Not ready after 2 minutes — see the errors above."
    exit 0
  fi

  if is_dev \
     && [ "${ZIZKADB_SEED_DEMO:-true}" != "false" ] && [ ! -f "$DEMO_MARKER" ]; then
    if ZIZKADB_SEED_API="http://127.0.0.1:${API_INTERNAL_PORT:-8000}" as_app python /opt/zizkadb/seed_demo.py > "$DATA/demo-chain.txt" 2>"$DATA/demo-seed.err"; then
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
  if is_dev; then
    echo "  Dashboard:  http://localhost:3001/dashboard/activity?agent=support-bot"
    echo "  API:        http://localhost:8000   (key: ${DEV_API_KEY})"
  else
    # Never print the token itself: platform log viewers show container output.
    echo "  Dashboard:  ${DASHBOARD_ADDRESS}   (sign in with the admin token)"
    echo "  API:        ${PUBLIC_API_URL}"
    echo ""
    if [ "$ADMIN_TOKEN_FROM_ENV" = true ]; then
      echo "  Admin token: the SELFHOST_ADMIN_TOKEN you set"
    else
      echo "  Admin token: docker exec <container> sed -n 's/^GEN_SELFHOST_ADMIN_TOKEN=//p' /data/secrets.env"
    fi
    echo "  Local one-click mode instead: start the container with -e ENV=development"
  fi
  echo "  Swagger:    ${PUBLIC_API_URL}/swagger"
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
