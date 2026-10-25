# Production deployment (self-hosted)

This page covers **running your own ZizkaDB instance in production** (VPS, private cloud, on-prem). For managed hosting, use [db.zizka.ai](https://db.zizka.ai/signup) — same SDK, no Docker ops.

For local dev, see [[Getting Started]] and [[Self-Hosting]].

---

## Before you go live

Run the config validator after editing `infra/.env`:

```bash
bash scripts/validate-selfhost-config.sh --production
```

Minimum checklist:

| Setting | Why |
|---------|-----|
| `ENV=production` | Disables dev API keys (`zizkadb_dev_local`) |
| `DEV_API_KEY` = unique random value | The API refuses to boot with an empty/default key in production; it is never accepted as auth there |
| `SELFHOST_ADMIN_TOKEN` | Dashboard sign-in for your server (long random value). Without it, dashboard login is disabled |
| `JWT_SECRET` | Generate with `openssl rand -hex 32` — not the default |
| `DEPLOYMENT_MODE=self_hosted` | Resolves self-host plan entitlements (1 API key cap when enforced) |
| `NEXT_PUBLIC_DEPLOYMENT_MODE=self_hosted` | Dashboard serves only sign-in + dashboard, no website/signup (default in the Docker image and `deploy-selfhost.sh`) |
| TLS in front of API + dashboard | nginx or your load balancer |

See [[Self-Hosting]] for the full Docker Compose flow and `bash infra/deploy-selfhost.sh`.

---

## Deploy / upgrade

On the server (from a git clone):

```bash
git pull origin main
bash infra/backup-postgres.sh    # always before upgrade
docker compose -f infra/docker-compose.yml up -d --build
bash scripts/smoke-test.sh
```

**Never** run `docker compose down -v` on a server with real data — it wipes Postgres volumes.

---

## Backups

```bash
bash infra/backup-postgres.sh   # → infra/backups/zizkadb_<timestamp>.sql.gz
bash infra/backup-qdrant.sh     # vector index snapshot
```

Retention defaults to 14 days (`BACKUP_KEEP_DAYS`). Schedule both via cron before deploys.

### Restore Postgres (disaster recovery)

1. Stop the API (avoid writes during restore):

   ```bash
   docker compose -f infra/docker-compose.yml stop api dashboard
   ```

2. Restore from the latest `.sql.gz`:

   ```bash
   gunzip -c infra/backups/zizkadb_YYYYMMDDTHHMMSSZ.sql.gz | \
     docker compose -f infra/docker-compose.yml exec -T postgres \
     psql -U zizkadb -d zizkadb
   ```

   For a **full replace**, drop and recreate the database first (destructive — only on empty/failed installs).

3. Restart the stack:

   ```bash
   docker compose -f infra/docker-compose.yml up -d
   bash scripts/smoke-test.sh
   ```

4. Re-index Qdrant if needed — events remain in Postgres `events.embedding`; Qdrant can be rebuilt from Postgres in a future maintenance script. After restore, verify search and `why()` against Postgres-backed routes first.

---

## Health checks

| Endpoint | Expected |
|----------|----------|
| `GET /health` | `{"status":"ok",...}` |
| `GET /health/deep` | Postgres, Redis, Qdrant status |

Use these for load balancer probes and post-deploy smoke tests.

---

## Managed cloud (db.zizka.ai)

The public managed service runs the same open-source runtime from this repository. Operator runbooks (EC2, nginx, PM2 dashboard) are maintained separately — OSS users only need the self-host steps above.

---

## Related

- [[Self-Hosting]] — first install, embeddings, suggestions API key
- [[Troubleshooting]] — auth, empty dashboard, search
- [SECURITY.md](https://github.com/ZIZKA-AI-SL/ZizkaDB/blob/main/SECURITY.md) — vulnerability reporting
