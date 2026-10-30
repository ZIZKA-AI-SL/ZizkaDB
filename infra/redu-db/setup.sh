#!/usr/bin/env bash
# Prepares a small private VPS as ZizkaDB's database: Postgres 16 + pgvector,
# tuned for 1 vCPU / 1 GB RAM / 2 GB disk, reachable only from the app's
# private network. Safe to run again: it never drops data and re-applies the
# same settings.
#
# Usage (as root, on Debian/Ubuntu):
#   ZIZKADB_DB_PASSWORD="$(openssl rand -hex 24)" APP_CIDR=10.0.0.0/8 bash setup.sh
# (a hex password needs no percent-encoding in DATABASE_URL)
#
# Optional: LISTEN_ADDR (default: this machine's private IPv4 addresses).
# Then start the app with
#   DATABASE_URL=postgresql://zizkadb:<password>@<this VPS private IP>:5432/zizkadb
set -euo pipefail

: "${ZIZKADB_DB_PASSWORD:?Set ZIZKADB_DB_PASSWORD to a long random password}"
: "${APP_CIDR:?Set APP_CIDR to the app VPS private address range, e.g. 10.0.0.0/8}"
PG_MAJOR=16

log() { echo "[zizkadb-db] $*"; }

# APP_CIDR goes into pg_hba.conf, so only an IPv4 range inside a private
# block is accepted (a keyword like "all", a bare IP or a public range is not).
# Host bits are cleared, e.g. 10.0.0.5/8 becomes 10.0.0.0/8.
cidr_re='^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})/([0-9]{1,2})$'
if ! [[ "$APP_CIDR" =~ $cidr_re ]]; then
  log "APP_CIDR=${APP_CIDR} is not an IPv4 range like 10.0.0.0/8."
  exit 1
fi
bits="${BASH_REMATCH[5]}"
ip=0
for i in 1 2 3 4; do
  octet="${BASH_REMATCH[$i]}"
  if [ "$((10#$octet))" -gt 255 ]; then log "APP_CIDR=${APP_CIDR} is not a valid address."; exit 1; fi
  ip=$(( (ip << 8) | 10#$octet ))
done
if [ "$((10#$bits))" -gt 32 ]; then log "APP_CIDR=${APP_CIDR} has a prefix above /32."; exit 1; fi
bits=$((10#$bits))
mask=$(( bits == 0 ? 0 : (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF ))
net=$(( ip & mask ))
in_block() { # in_block <network as int> <prefix>: APP_CIDR lies inside it
  local bmask=$(( (0xFFFFFFFF << (32 - $2)) & 0xFFFFFFFF ))
  [ "$bits" -ge "$2" ] && [ $(( net & bmask )) -eq "$1" ]
}
if ! { in_block $((10 << 24)) 8 || in_block $(( (172 << 24) | (16 << 16) )) 12 \
    || in_block $(( (192 << 24) | (168 << 16) )) 16 || in_block $(( (100 << 24) | (64 << 16) )) 10; }; then
  log "APP_CIDR=${APP_CIDR} is not inside a private range (10.0.0.0/8, 172.16.0.0/12,"
  log "  192.168.0.0/16 or 100.64.0.0/10). Use the app VPS private range."
  exit 1
fi
APP_CIDR="$(( net >> 24 )).$(( (net >> 16) & 255 )).$(( (net >> 8) & 255 )).$(( net & 255 ))/${bits}"
unset cidr_re ip octet mask net i

# Private addresses only: never listen on a public interface.
if [ -z "${LISTEN_ADDR:-}" ]; then
  # "|| true": no match is handled just below, with a message.
  LISTEN_ADDR="$(hostname -I | tr ' ' '\n' \
    | grep -E '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.)' \
    | paste -sd, - || true)"
fi
if [ -z "$LISTEN_ADDR" ]; then
  log "No private IPv4 address found. Set LISTEN_ADDR."
  exit 1
fi
# It is written into postgresql.conf: addresses only, and never "all of them".
case ",${LISTEN_ADDR}," in
  *[!0-9A-Fa-f.:,]*|*,0.0.0.0,*|*,::,*|*,,*)
    log "LISTEN_ADDR=${LISTEN_ADDR} must list this machine's private addresses, comma-separated."
    exit 1 ;;
esac

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
CONF_FILE="${CONF_DIR}/conf.d/zizkadb.conf"
OLD_CONF="$(cat "$CONF_FILE" 2>/dev/null || true)"
cat > "${CONF_FILE}.new" <<CONF
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
mv "${CONF_FILE}.new" "$CONF_FILE"

# Only the app's private range, only this database and role, password auth.
# Written to a new file and moved into place, so pg_hba.conf is never partial.
HBA="${CONF_DIR}/pg_hba.conf"
OLD_HBA="$(cat "$HBA")"
{ grep -v '# zizkadb-app$' "$HBA" || true
  echo "host zizkadb zizkadb ${APP_CIDR} scram-sha-256 # zizkadb-app"; } > "${HBA}.new"
chown --reference="$HBA" "${HBA}.new"
chmod --reference="$HBA" "${HBA}.new"
mv "${HBA}.new" "$HBA"

# Postgres binds the private address, so on boot it must start after the
# network is up (Debian orders it on network.target only).
UNIT_DIR=/etc/systemd/system/postgresql@.service.d
mkdir -p "$UNIT_DIR"
printf '[Unit]\nWants=network-online.target\nAfter=network-online.target\n' > "${UNIT_DIR}/zizkadb.conf"
systemctl daemon-reload
systemctl enable postgresql >/dev/null

# A re-run only to rotate the password keeps the app's connections: restart
# only when a server setting changed, reload when only pg_hba.conf did.
if [ "$OLD_CONF" != "$(cat "$CONF_FILE")" ] || ! systemctl is-active --quiet "postgresql@${PG_MAJOR}-main"; then
  systemctl restart "postgresql@${PG_MAJOR}-main"
elif [ "$OLD_HBA" != "$(cat "$HBA")" ]; then
  systemctl reload "postgresql@${PG_MAJOR}-main"
fi
unset OLD_CONF OLD_HBA

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
