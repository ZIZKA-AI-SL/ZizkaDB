#!/usr/bin/env bash
# Prepares a small private VPS as ZizkaDB's database: Postgres 16 + pgvector,
# tuned for 1 vCPU / 1 GB RAM / 2 GB disk, reachable only from the app's
# private network. Safe to run again: it never drops data and re-applies the
# same settings.
#
# Usage (as root, on Debian/Ubuntu):
#   ZIZKADB_DB_PASSWORD='<long random>' APP_CIDR=10.0.0.0/8 bash setup.sh
#
# Optional: LISTEN_ADDR (default: this machine's private IPv4 addresses).
# Then start the app with
#   DATABASE_URL=postgresql://zizkadb:<password>@<this VPS private IP>:5432/zizkadb
set -euo pipefail

: "${ZIZKADB_DB_PASSWORD:?Set ZIZKADB_DB_PASSWORD to a long random password}"
: "${APP_CIDR:?Set APP_CIDR to the app VPS private address range, e.g. 10.0.0.0/8}"
case "$APP_CIDR" in
  0.0.0.0/*|::/*|*/0)
    echo "[zizkadb-db] APP_CIDR=${APP_CIDR} allows any address. Use the app VPS private range."
    exit 1 ;;
esac
PG_MAJOR=16

log() { echo "[zizkadb-db] $*"; }

# Private addresses only: never listen on a public interface.
if [ -z "${LISTEN_ADDR:-}" ]; then
  LISTEN_ADDR="$(hostname -I | tr ' ' '\n' \
    | grep -E '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)' | paste -sd, -)"
fi
if [ -z "$LISTEN_ADDR" ]; then
  log "No private IPv4 address found. Set LISTEN_ADDR."
  exit 1
fi

# ── Packages: PGDG repository (Postgres 16 and its pgvector build) ───────────
if ! command -v "/usr/lib/postgresql/${PG_MAJOR}/bin/postgres" >/dev/null 2>&1; then
  log "Installing Postgres ${PG_MAJOR} and pgvector..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q --no-install-recommends postgresql-common ca-certificates
  /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
  apt-get install -y -q --no-install-recommends \
    "postgresql-${PG_MAJOR}" "postgresql-${PG_MAJOR}-pgvector"
  apt-get clean
  rm -rf /var/lib/apt/lists/*
fi

CONF_DIR="/etc/postgresql/${PG_MAJOR}/main"

# ── Settings for 1 GB RAM and a 2 GB disk ─────────────────────────────────────
# A small WAL keeps disk use bounded (the default allows 1 GB of WAL).
cat > "${CONF_DIR}/conf.d/zizkadb.conf" <<CONF
listen_addresses = '${LISTEN_ADDR}'
max_connections = 40
shared_buffers = 128MB
effective_cache_size = 512MB
work_mem = 4MB
maintenance_work_mem = 64MB
max_wal_size = 256MB
min_wal_size = 64MB
wal_keep_size = 0
jit = off
password_encryption = scram-sha-256
log_min_duration_statement = 2000
CONF

# Only the app's private range, only this database and role, password auth.
HBA="${CONF_DIR}/pg_hba.conf"
sed -i '/# zizkadb-app$/d' "$HBA"
echo "host zizkadb zizkadb ${APP_CIDR} scram-sha-256 # zizkadb-app" >> "$HBA"

systemctl enable postgresql >/dev/null
systemctl restart postgresql

# ── Role, database and extensions ─────────────────────────────────────────────
# The role owns its database but is not a superuser. The extensions are
# created here because creating them needs a superuser.
cd /tmp
PG_ROLE_PASSWORD="$ZIZKADB_DB_PASSWORD" runuser -u postgres -- \
  psql -q -X -v ON_ERROR_STOP=1 -d postgres <<'SQL'
\getenv pw PG_ROLE_PASSWORD
SELECT format('CREATE ROLE zizkadb LOGIN PASSWORD %L', :'pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zizkadb') \gexec
ALTER ROLE zizkadb LOGIN NOSUPERUSER PASSWORD :'pw';
SELECT 'CREATE DATABASE zizkadb OWNER zizkadb'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'zizkadb') \gexec
SQL
runuser -u postgres -- psql -q -X -v ON_ERROR_STOP=1 -d zizkadb <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
SQL

log "Ready on ${LISTEN_ADDR}:5432 for ${APP_CIDR}."
