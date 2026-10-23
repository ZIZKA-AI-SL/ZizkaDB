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

## Dashboard login

A self-hosted dashboard is only your dashboard: there is no website, signup, pricing or email login. Opening http://localhost:3001/ takes you straight to the sign-in page (or into the dashboard if you're already signed in). Accounts and plans live on the managed cloud at zizka.ai.

- **Local (`ENV=development`):** click **Open my dashboard →**. No email, no token.
- **Server (`ENV=production`):** the same page asks for an admin token. Set `SELFHOST_ADMIN_TOKEN` in `infra/.env` (generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`) and restart the API. Without it, dashboard login is disabled — it never falls back to one-click.

Both sign in as the instance's single owner tenant, so your existing data stays visible. Then create agents and keys like production.

Notes for servers:

- The token must be at least 16 characters — the API refuses to start otherwise. Use a long random value; sign-in attempts are rate-limited (10/min per IP), but the token itself is what keeps people out.
- Anyone with the token gets full access to the one owner workspace. There are no per-user accounts on a self-hosted instance.
- Sessions last 7 days. Changing `SELFHOST_ADMIN_TOKEN` stops new sign-ins with the old token. To also sign out existing sessions, rotate `JWT_SECRET` and restart the API.
- Put the API behind a reverse proxy (nginx) that sets `X-Forwarded-For`, so rate limiting sees real client IPs. The API only trusts that header from addresses in `TRUSTED_PROXIES` (default `127.0.0.1,::1,172.16.0.0/12`, which covers nginx on the host reaching the container through the Docker bridge). If another proxy or load balancer sits in front of nginx, add its address, otherwise all users share one rate-limit bucket.

> If you previously signed in to a production self-host with email OTP, that data belongs to your OTP account's tenant, not the owner tenant. Open an issue if you need help moving it.

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

## Deploy on Redu Cloud

The single-container image deploys to [Redu Cloud](https://redu.cloud) from this repository, unchanged. Add the redu MCP to your coding agent and ask it to "Deploy https://github.com/Zizka-ai/ZizkaDB to redu". The recipe it follows is [`redu.md`](https://github.com/Zizka-ai/ZizkaDB/blob/main/redu.md):

- one VM, building `infra/allinone/Dockerfile`
- port 3001 public, serving the dashboard with the API under `/v1`
- `/data` kept between deploys
- `ZIZKADB_PUBLIC_URL` set to the deployment URL, and `TRUSTED_PROXIES` covering the platform proxy

After it is live:

1. Read the admin token over SSH: `sudo podman exec redu-app sed -n 's/^GEN_SELFHOST_ADMIN_TOKEN=//p' /data/secrets.env`.
2. Sign in with the token and create the API key in Settings.
3. Point your agent at the deployment with `ZIZKADB_HOST` and `ZIZKADB_API_KEY`.

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
- `SELFHOST_ADMIN_TOKEN` — required to sign in to the dashboard when `ENV=production` (no email/SMTP needed)
- `ENV=production` (disables dev key bypass). `infra/docker-compose.yml` requires `ENV` and stops with an error if it is missing; never use `ENV=development` on a server
- `DEV_API_KEY` — set a unique random value (`openssl rand -hex 24`) — the API refuses to start in production if it is empty or the default, and never accepts it as auth there
- `DEPLOYMENT_MODE=self_hosted` — keep this set even in production. `ENV=production` alone doesn't distinguish a self-hosted install from managed cloud (both use it), so this separate flag is what makes plan-based entitlement checks (e.g. API key limits) resolve to the Self-Hosted plan instead of whatever is in `users.plan`.
- `EMBEDDINGS_ENABLED=false` — default for self-host. Set to `true` only when you want semantic search / vector indexing; then configure `OPENAI_API_KEY` (or your provider key in Dashboard → Settings).
- **One agent, one API key.** A self-hosted install runs a single agent with a single active API key; this is always enforced and cannot be changed by env vars. The first agent name you log with takes the slot (the `support-bot` demo agent does not count). Agents that already existed before upgrading keep working, but no new ones can be added. Multiple agents, agent fleets and agent-to-agent messaging are on [ZizkaDB Cloud](https://db.zizka.ai).

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
