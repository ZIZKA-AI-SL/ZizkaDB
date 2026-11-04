---
name: zizkadb-dev-setup
description: Set up and start the local ZizkaDB development stack. Use when setting up ZizkaDB for the first time, restarting the local stack, or debugging service connectivity.
---

# ZizkaDB — Local Dev Setup

## First-time setup (one command)

```bash
git clone https://github.com/ZIZKA-AI-SL/ZizkaDB.git && cd ZizkaDB
bash scripts/setup-local.sh
```

This starts all services using Docker Compose (or falls back to native if Docker is unavailable) and runs the causal lineage demo.

## What starts

| Service | URL | Purpose |
|---|---|---|
| API | http://localhost:8000 | FastAPI backend |
| Swagger UI | http://localhost:8000/swagger | Interactive API explorer |
| Dashboard | http://localhost:3001 | Next.js dashboard |
| Postgres | localhost:5432 | Primary database |
| Qdrant | http://localhost:6333 | Vector search |
| Redis | localhost:6379 | Embedding cache |

## Dev login (no email required)

1. Open http://localhost:3001/login
2. Click **"Open my dashboard"** — no OTP needed in dev mode

## Dev API key

`zizkadb_dev_local` — automatically accepted by the API when `ENV=development`.

```bash
curl -H "Authorization: Bearer zizkadb_dev_local" http://localhost:8000/health
```

## Quick Python smoke test

```python
import asyncio
from zizkadb import ZizkaDB

async def main():
    async with ZizkaDB(host="http://localhost:8000") as db:
        event = await db.log(agent="test", event="smoke", data={"ok": True})
        print(event.event_id)

asyncio.run(main())
```

## Dev vs production Docker Compose

```bash
# Dev (with --reload and code volume mount):
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml up -d

# Production (--workers 4, no reload):
docker compose -f infra/docker-compose.yml up -d
```

## Reset local database

```bash
bash scripts/reset-local-db.sh
# WARNING: wipes all local data — development only
```

## Environment file

Copy `infra/.env.example` → `infra/.env` and fill in:
- `OPENAI_API_KEY` — required for `db.search()` and `db.context_for()`
- `JWT_SECRET` — generate: `openssl rand -hex 32`
- Other vars have safe defaults for local dev
