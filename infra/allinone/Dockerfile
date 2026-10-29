# syntax=docker/dockerfile:1
# ZizkaDB single-container image: Postgres 16 (pgvector) + Redis + API + dashboard.
# One `docker run`, no compose file, no .env. All state lives in the /data volume.
#
# Production by default: sign-in needs the admin token generated on first start
# (docker exec <container> sed -n 's/^GEN_SELFHOST_ADMIN_TOKEN=//p' /data/secrets.env).
# Port 3001 serves the dashboard and the API (/v1) together; 8000 is the API alone.
# Local one-click mode, with the well-known zizkadb_dev_local API key:
#   docker run -e ENV=development -p 127.0.0.1:8000:8000 -p 127.0.0.1:3001:3001 ...
# Multi-container or semantic search (Qdrant): use infra/docker-compose.yml.
#
# Build from the repo root:
#   docker build -f infra/allinone/Dockerfile -t zizkadb .

FROM node:25-bookworm-slim AS dashboard
WORKDIR /app
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm ci --legacy-peer-deps
COPY dashboard/ .
# Empty API URL: the browser calls its own origin, and the image's front proxy
# (front-proxy.js) sends /v1 to the API, so one public port serves everything.
ENV NEXT_PUBLIC_API_URL="" \
    NEXT_PUBLIC_DEV_MODE=false \
    NEXT_PUBLIC_DEPLOYMENT_MODE=self_hosted \
    NEXT_TELEMETRY_DISABLED=1
RUN mkdir -p ./public && npm run build

FROM python:3.12-slim-bookworm AS api-deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
COPY core/requirements.txt .
RUN /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim-bookworm

# postgresql-common is installed first so the package does not create a
# default cluster at build time; the entrypoint creates it in the /data volume.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl tini redis-server postgresql-common \
    && install -d /usr/share/postgresql-common/pgdg \
    && sed -ri 's/#(create_main_cluster) .*$/\1 = false/' /etc/postgresql-common/createcluster.conf \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        postgresql-16 postgresql-16-pgvector \
    # LLVM (~115 MB) is only loaded for JIT, which the entrypoint turns off.
    && rm -f /usr/lib/*/libLLVM* \
    && rm -rf /var/lib/apt/lists/*

COPY --from=dashboard /usr/local/bin/node /usr/local/bin/node
COPY --from=api-deps /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:/usr/lib/postgresql/16/bin:$PATH"

RUN addgroup --system zizkadb && adduser --system --ingroup zizkadb zizkadb

COPY --chown=zizkadb:zizkadb core/ /app/api/
COPY --from=dashboard --chown=zizkadb:zizkadb /app/public /app/dashboard/public
COPY --from=dashboard --chown=zizkadb:zizkadb /app/.next/standalone /app/dashboard/
COPY --from=dashboard --chown=zizkadb:zizkadb /app/.next/static /app/dashboard/.next/static
COPY core/db/schema.sql infra/allinone/entrypoint.sh infra/allinone/seed_demo.py \
     infra/allinone/front-proxy.js /opt/zizkadb/
RUN chmod +x /opt/zizkadb/entrypoint.sh

# ENV=production unless the operator passes ENV=development. The entrypoint
# generates the database password, JWT secrets and SELFHOST_ADMIN_TOKEN once and
# keeps them in /data/secrets.env. QDRANT_URL="" runs without Qdrant (embeddings
# are off by default on self-host). Only the in-container front proxy is
# trusted for X-Forwarded-For; behind another proxy, add its address range.
ENV ENV=production \
    DEPLOYMENT_MODE=self_hosted \
    EMBEDDINGS_ENABLED=false \
    TRUSTED_PROXIES=127.0.0.1,::1 \
    REDIS_URL=redis://127.0.0.1:6379 \
    QDRANT_URL="" \
    COMMUNITY_UPLOAD_DIR=/data/community_uploads \
    NEXT_TELEMETRY_DISABLED=1

VOLUME /data
# 3001 is the front proxy (FRONT_PORT); EXPOSE is static, so a custom
# FRONT_PORT must be published explicitly with -p.
EXPOSE 8000 3001

HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=6 \
    CMD curl -fsS "http://127.0.0.1:${API_INTERNAL_PORT:-8000}/health" >/dev/null \
        && curl -fsS -o /dev/null "http://127.0.0.1:${FRONT_PORT:-3001}/login" || exit 1

ENTRYPOINT ["tini", "--", "/opt/zizkadb/entrypoint.sh"]
