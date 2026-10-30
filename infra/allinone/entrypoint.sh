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
# DATABASE_URL, read once. Local (no host, a loopback host, or the local
# socket): this container runs Postgres and the URL may set the zizkadb role's
# password. External (any other host): no local Postgres runs, and psql and
# pg_isready get the same URL without its password (DB_CONNINFO) plus the
# password on their own environment only (DB_PASSWORD), so every connection
# setting asyncpg uses also applies to them and the password reaches no argv.
# A URL that cannot be read stops the start with a message that quotes no part
# of it: it holds the password.
PG_PASSWORD="$GEN_PG_PASSWORD"
DB_REMOTE=false
DB_CONNINFO=""
DB_PASSWORD=""
if [ -n "${DATABASE_URL:-}" ]; then
  if ! db_env="$(python - <<'PY'
import os
import shlex
import sys
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
# Query keys libpq and asyncpg both read. Others are asyncpg server settings,
# which libpq would refuse and the schema load does not need.
LIBPQ_KEYS = {
    "host", "port", "dbname", "user", "passfile", "sslmode", "sslrootcert",
    "sslcert", "sslkey", "sslcrl", "sslpassword", "ssl_min_protocol_version",
    "ssl_max_protocol_version", "target_session_attrs", "connect_timeout",
    "application_name", "options", "krbsrvname", "gsslib",
}


def fail(reason):
    print(f"[zizkadb] DATABASE_URL {reason}. Percent-encode special characters "
          "in the user name and password (for example / as %2F, @ as %40).", file=sys.stderr)
    sys.exit(1)


try:
    u = urlsplit(os.environ["DATABASE_URL"].strip())
    query = parse_qsl(u.query, keep_blank_values=True)
except ValueError:
    fail("could not be read")
if u.scheme not in ("postgresql", "postgres"):
    fail("must start with postgresql://")
userinfo, at, hostlist = u.netloc.rpartition("@")
user, _, password = userinfo.partition(":")
hosts = []
for part in hostlist.split(","):
    host, port = part, ""
    if part.startswith("["):
        host, _, rest = part[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    elif ":" in part:
        host, _, port = part.rpartition(":")
    if port and not (port.isdigit() and 0 < int(port) < 65536):
        fail("has a port that is not a number")
    if host:
        hosts.append(unquote(host))
password = unquote(password)
kept = []
for key, value in query:
    if key == "host":
        hosts += [h for h in value.split(",") if h]
    if key == "password":
        password = password or value
    elif key == "database":
        kept.append(("dbname", value))
    elif key in LIBPQ_KEYS:
        kept.append((key, value))
# A path host is a Unix socket: on its own it means this container.
remote = any(h.lower() not in LOOPBACK and not h.startswith("/") for h in hosts)
out = {"DB_REMOTE": "true" if remote else "false"}
if remote:
    netloc = (quote(unquote(user), safe="") + "@" if user else "") + hostlist
    out["DB_CONNINFO"] = urlunsplit(("postgresql", netloc, u.path, urlencode(kept), ""))
    out["DB_PASSWORD"] = password
    out["DB_WHERE"] = ",".join(hosts)
elif unquote(user) == "zizkadb" and password:
    out["PG_PASSWORD"] = password
for key, value in out.items():
    print(f"{key}={shlex.quote(value)}")
PY
)"; then
    exit 1
  fi
  eval "$db_env"
  unset db_env
fi
export DATABASE_URL="${DATABASE_URL:-postgresql://zizkadb:${PG_PASSWORD}@127.0.0.1:5432/zizkadb}"
# psql and pg_isready against the external database; nothing else sees PG*.
remote_pg() {
  local cmd="$1"
  shift
  as_app env PGPASSWORD="$DB_PASSWORD" PGCONNECT_TIMEOUT=10 "$cmd" -d "$DB_CONNINFO" "$@"
}
if is_dev; then
  # Local one-click mode keeps the well-known key the README and SDK examples use.
  export DEV_API_KEY="${DEV_API_KEY:-zizkadb_dev_local}"
elif [ "${DEV_API_KEY:-}" = "zizkadb_dev_local" ] || [ "${DEV_API_KEY:-}" = "agdb_dev_local" ]; then
  # The API refuses to start in production with a well-known dev key (and
  # never accepts dev keys there anyway). Drop it instead of a start-up loop.
  log "Ignoring the well-known DEV_API_KEY: it works only in local mode (-e ENV=development)."
  unset DEV_API_KEY
fi
unset GEN_JWT_SECRET GEN_JWT_REFRESH_SECRET GEN_DEV_API_KEY GEN_SELFHOST_ADMIN_TOKEN GEN_PG_PASSWORD

# Public address (optional): the base for links the API generates. The front
# proxy serves the dashboard and the API (/v1) on one port, so that one
# address is enough.
FRONT_PORT="${FRONT_PORT:-3001}"
case "$FRONT_PORT" in
  ''|*[!0-9]*|0*) log "FRONT_PORT must be a port number (1-65535), got '${FRONT_PORT}'."; exit 1 ;;
esac
if [ "$FRONT_PORT" -gt 65535 ]; then log "FRONT_PORT must be 65535 or lower."; exit 1; fi
for internal in "${API_INTERNAL_PORT:-8000}" "${DASHBOARD_INTERNAL_PORT:-3002}" 5432 6379; do
  if [ "$FRONT_PORT" = "$internal" ]; then
    log "FRONT_PORT=${FRONT_PORT} is already used inside the container (API ${API_INTERNAL_PORT:-8000}, dashboard ${DASHBOARD_INTERNAL_PORT:-3002}, Postgres 5432, Redis 6379). Choose another."
    exit 1
  fi
done
export FRONT_PORT
DASHBOARD_ADDRESS="http://localhost:${FRONT_PORT}"
if [ -n "${ZIZKADB_PUBLIC_URL:-}" ]; then
  DASHBOARD_ADDRESS="${ZIZKADB_PUBLIC_URL%/}"
  export PUBLIC_API_URL="${PUBLIC_API_URL:-$DASHBOARD_ADDRESS}"
elif is_dev; then
  # Local mode publishes the API on :8000, where the SDKs look by default.
  export PUBLIC_API_URL="${PUBLIC_API_URL:-http://localhost:8000}"
fi
# Without a public URL, production leaves PUBLIC_API_URL unset so the API
# builds links from each request's own address (behind any domain or proxy).
# The dashboard calls the API on its own origin, so browsers may call the API
# only from that origin. This matters most in local mode, where the API also
# accepts the well-known dev key. Set CORS_ALLOWED_ORIGINS to allow others.
export CORS_ALLOWED_ORIGINS="${CORS_ALLOWED_ORIGINS:-$DASHBOARD_ADDRESS}"

# ── External database: wait for it, load the schema once ─────────────────────
# The check runs in the database, not against a marker file, so a new or wiped
# /data never reloads the schema over existing data (schema.sql is not
# re-runnable). One transaction under an advisory lock: two containers starting
# together load it once, and a failed load leaves nothing half-made.
if [ "$DB_REMOTE" = true ]; then
  log "Using the external database at ${DB_WHERE}."
  if [ -s "$PGDATA/PG_VERSION" ]; then
    log "Note: /data/postgres holds a local database from an earlier start, which this"
    log "  container no longer uses. To move its data, start once without DATABASE_URL"
    log "  and copy it with pg_dump into the external database."
  fi
  db_up=0
  db_deadline=$((SECONDS + 120))
  while [ "$SECONDS" -lt "$db_deadline" ]; do
    if remote_pg pg_isready -q -t 2 >/dev/null 2>&1; then db_up=1; break; fi
    sleep 1
  done
  if [ "$db_up" -ne 1 ]; then
    log "The external database at ${DB_WHERE} is not reachable after 2 minutes."
    log "  Check its address, that it listens on the private network, and its firewall."
    exit 1
  fi
  unset db_up db_deadline
  # The tables schema.sql creates: some of them without events means an
  # earlier, partial setup, which schema.sql cannot finish (plain CREATE TABLE).
  schema_tables="$(sed -nE 's/^CREATE TABLE (IF NOT EXISTS )?([a-z_]+).*/\2/p' /opt/zizkadb/schema.sql | paste -sd, -)"
  if ! db_state="$(remote_pg psql -X -q -t -A -v ON_ERROR_STOP=1 -v tables="$schema_tables" 2>&1 <<'SQL'
SELECT CASE
  WHEN to_regclass('public.events') IS NOT NULL THEN 'ready'
  WHEN EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public'
               AND tablename = ANY (string_to_array(:'tables', ','))) THEN 'partial'
  WHEN NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)
       AND (SELECT count(*) FROM pg_extension WHERE extname IN ('vector', 'uuid-ossp')) < 2
    THEN 'extensions'
  ELSE 'empty'
END;
SQL
)"; then
    log "Could not sign in to the external database. Check the user name, password and"
    log "  database name in DATABASE_URL, and that the database allows this machine:"
    printf '%s\n' "$db_state" | sed 's/^/[zizkadb]   /'
    exit 1
  fi
  case "$db_state" in
    ready) ;;
    partial)
      log "The external database has some ZizkaDB tables but no events table: an earlier"
      log "  setup did not finish. Point DATABASE_URL at an empty database, or restore it."
      exit 1 ;;
    extensions)
      log "The external database needs the vector and uuid-ossp extensions, and this role"
      log "  cannot create them. Run as a superuser in that database:"
      log "  CREATE EXTENSION vector; CREATE EXTENSION \"uuid-ossp\";  (infra/redu-db/setup.sh does this)"
      exit 1 ;;
    empty)
      # One transaction under an advisory lock: containers starting together
      # load the schema once (the second sees events and skips), and a failed
      # load leaves nothing half-made. The lock ID sits next to init_db's
      # MIGRATION_LOCK_ID in core/db/connection.py, so the two never collide.
      log "Loading the schema into the external database..."
      if ! remote_pg psql -X -q -1 -v ON_ERROR_STOP=1 >/dev/null <<'SQL'
SET client_min_messages = warning;
SELECT pg_advisory_xact_lock(7301884223);
SELECT to_regclass('public.events') IS NULL AS need_schema \gset
\if :need_schema
\i /opt/zizkadb/schema.sql
\endif
SQL
      then
        log "Loading the schema failed (see the error above). Nothing was created."
        exit 1
      fi ;;
    *)
      log "Unexpected answer from the external database: ${db_state}"
      exit 1 ;;
  esac
  unset db_state schema_tables
fi
unset DB_PASSWORD DB_CONNINFO

# ── Postgres: create the cluster and schema on first start ───────────────────
if [ "$DB_REMOTE" != true ]; then
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
fi
unset PG_PASSWORD PG_PASSWORD_HASH

# ── Run everything ───────────────────────────────────────────────────────────
PIDS=()
PG_PID=""

shutdown() {
  trap - TERM INT
  log "Stopping..."
  for pid in "${PIDS[@]}" "${BANNER_PID:-}"; do
    [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null || true
  done
  # Fast shutdown: disconnect clients and flush, without waiting on them.
  if [ "$DB_REMOTE" != true ]; then
    as_pg pg_ctl -D "$PGDATA" -m fast -w stop >/dev/null 2>&1 || true
  fi
  wait || true
}
trap 'shutdown; exit 0' TERM INT

# Started with exec (not through the as_* functions) so $! is the service
# itself and signals reach it.
if [ "$DB_REMOTE" != true ]; then
  ( exec "${PG_USER[@]}" postgres -D "$PGDATA" \
      -c listen_addresses=127.0.0.1 \
      -c jit=off \
      -c unix_socket_directories=/var/run/postgresql ) &
  PG_PID=$!
fi

( exec "${APP_USER[@]}" redis-server --bind 127.0.0.1 --port 6379 --dir "$DATA/redis" \
    --appendonly yes --daemonize no --logfile "" >/dev/null ) &
PIDS+=($!)

if [ "$DB_REMOTE" != true ]; then
  for _ in $(seq 1 60); do
    pg_isready -q -h 127.0.0.1 -U zizkadb -d zizkadb && break
    sleep 0.5
  done
fi

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
    echo "  Dashboard:  ${DASHBOARD_ADDRESS}/dashboard/activity?agent=support-bot"
    echo "  API:        ${PUBLIC_API_URL}   (key: ${DEV_API_KEY})"
  else
    # Never print the token itself: platform log viewers show container output.
    echo "  Dashboard:  ${DASHBOARD_ADDRESS}   (sign in with the admin token)"
    echo "  API:        ${PUBLIC_API_URL:-$DASHBOARD_ADDRESS} (same address, under /v1)"
    echo ""
    if [ "$ADMIN_TOKEN_FROM_ENV" = true ]; then
      echo "  Admin token: the SELFHOST_ADMIN_TOKEN you set"
    else
      echo "  Admin token: docker exec <container> sed -n 's/^GEN_SELFHOST_ADMIN_TOKEN=//p' /data/secrets.env"
    fi
    echo "  Local one-click mode instead: start the container with -e ENV=development"
  fi
  echo "  Swagger:    ${PUBLIC_API_URL:-$DASHBOARD_ADDRESS}/swagger"
  echo ""
) &
BANNER_PID=$!

# Exit as soon as any service dies so Docker's restart policy can act on it.
set +e
wait -n ${PG_PID:+"$PG_PID"} "${PIDS[@]}"
code=$?
set -e
log "A service exited (status $code); shutting down."
shutdown
exit "$code"
