# Self-Hosting

Run ZizkaDB on your laptop or VPS with Docker or a native fallback when Docker is unavailable.

## OSS quickstart (recommended)

### Docker (recommended — matches production)

```bash
git clone https://github.com/Zizka-ai/ZizkaDB.git && cd ZizkaDB
bash scripts/quickstart.sh
```

One command: Docker stack (pre-built GHCR images when available) + `db.why()` demo + dashboard link.

Stack only:

```bash
bash scripts/setup-local.sh
```

Connect your agent: [CONNECT.md](https://github.com/Zizka-ai/ZizkaDB/blob/main/CONNECT.md)

Requires Docker Desktop or OrbStack running in a **native arm64 Terminal** (not Rosetta on Apple Silicon).

Or manually:

```bash
cp .env.example infra/.env
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dashboard.yml up -d
```

### Native fallback (no Docker)

When container runtime is unavailable:

```bash
bash scripts/bootstrap-local.sh
bash scripts/restart-native-stack.sh
```

Stop native stack:

```bash
bash scripts/stop-native-stack.sh
```

## Services

| Service | Default URL |
|---------|-------------|
| API | http://localhost:8000 |
| Deep health | http://localhost:8000/health/deep (HTTP 503 when Postgres, Redis, or — with embeddings on — Qdrant is down) |
| Swagger | http://localhost:8000/swagger |
| Dashboard | http://localhost:3001 |
| Postgres | localhost:5432 (native) or internal (Docker) |
| Redis | localhost:6379 (native) or internal (Docker) |
| Qdrant | localhost:6333 (native) or internal (Docker) |

## Dashboard login (local)

1. Open http://localhost:3001/login
2. Click **Open my dashboard →** (dev mode, no email)
3. Create agents and keys like production

For local smoke testing, prefer `npm run build && npx next start -p 3001` (used by `restart-native-stack.sh`). `npm run dev` requires **Node 20+** (see `dashboard/.nvmrc`).

## SDK connection

```python
db = ZizkaDB(host="http://localhost:8000")
```

MCP:

```json
"env": { "ZIZKADB_HOST": "http://localhost:8000" }
```

## Validation

```bash
bash scripts/validate-selfhost-config.sh   # env + connectivity
bash scripts/smoke-test.sh               # health, log, optional search
```

Optional: seed drift/baseline test data with `python scripts/seed-support-bot-events.py`.

Integration tests (stack must be running):

```bash
ZIZKADB_RUN_INTEGRATION=1 .venv/bin/pytest -m integration core/tests/test_integration_selfhost.py -v
```

## Production self-host

Self-host deploy uses `infra/deploy-selfhost.sh` and Docker Compose.

Managed cloud (`db.zizka.ai`) deploys from the private `zizkadb-cloud` repository — **not** this public product repo.

```bash
bash infra/deploy-selfhost.sh
```

**Never** `docker compose down -v` on a server with real users.

## Staging (prod-like rehearsal)

Same host can run a second Compose project with isolated volumes and ports. Templates and deploy order: [docs/staging.md](../docs/staging.md).

```bash
export COMPOSE_PROJECT_NAME=zizkadb-staging
docker compose -f infra/docker-compose.yml -f infra/docker-compose.staging.yml \
  --env-file infra/.env.staging up -d --build
ZIZKADB_API_KEY='...' bash scripts/smoke-test.sh https://staging-db.zizka.ai
```

Use `ENV=production`, `NEXT_PUBLIC_DEV_MODE=false`, and **different** JWT secrets than production.

Local laptop reset only:

```bash
bash scripts/reset-local-db.sh
```

Configure in `infra/.env`:

- `DATABASE_URL`
- `REDIS_URL`
- `QDRANT_URL`
- `JWT_SECRET` / `JWT_REFRESH_SECRET`
- `EMAIL_*` for OTP login
- `ENV=production` (disables dev key bypass)
- **Do not set** `DEV_API_KEY` in production
- `DEPLOYMENT_MODE=self_hosted` — keep this set even in production. `ENV=production` alone doesn't distinguish a self-hosted install from managed cloud (both use it), so this separate flag is what makes plan-based entitlement checks (e.g. API key limits) resolve to the Self-Hosted plan instead of whatever is in `users.plan`.
- `EMBEDDINGS_ENABLED=false` — default for self-host. Set to `true` only when you want semantic search / vector indexing; then configure `OPENAI_API_KEY` (or your provider key in Dashboard → Settings).
- `API_KEY_LIMITS_ENFORCED=true` — enable per-plan API key caps (Self-Hosted: 1, Pro: 2, Team: 5). Default `false`; self-hosted installs are uncapped unless you set this.

Validate production config before deploy:

```bash
bash scripts/validate-selfhost-config.sh --production
```

## Embeddings

Set `OPENAI_API_KEY` in `infra/.env` for semantic search, memory context, and drift embeddings. Logging and `why()` work without it.

Dashboard → Settings → choose embedding model (OpenAI platform key or bring your own).

## AI Suggestions

The **Suggestions** tab generates evidence-grounded recommendations from an agent's recorded
behavior using the Claude API. Set `ANTHROPIC_API_KEY` in `infra/.env` to enable it:

```bash
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-6   # optional; this is the default
```

Without a key the endpoint returns `status: "ai_not_configured"` (HTTP 200) and the dashboard shows a
setup card — every other feature keeps working. Advanced overrides (rarely needed):
`ANTHROPIC_BASE_URL` (default `https://api.anthropic.com`) and `ANTHROPIC_VERSION` (default
`2023-06-01`). Suggestions are computed on demand, cached in Redis by an evidence fingerprint, and
per-tenant rate-limited.

## Backups

```bash
bash infra/backup-postgres.sh
bash infra/backup-qdrant.sh
```

See [Production-Deployment.md](Production-Deployment.md) for restore steps.
