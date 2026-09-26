# ZizkaDB — Claude Code Guide

ZizkaDB is a causal event database for AI agents. It lets you log every agent action, trace exactly why a decision was made (`db.why()`), time-travel to any past state (`db.at()`), and inject relevant memory into future runs (`db.context_for()`). It is an open-source (AGPL-3.0) FastAPI + Next.js 14 product deployed on a single EC2 instance.

---

## Stack at a glance

| Layer | Technology | Entry point |
|---|---|---|
| API | FastAPI 0.111 + asyncpg (no ORM) | `core/main.py` — 13 routers at `/v1/` |
| Database | PostgreSQL 16 + pgvector | `core/db/schema.sql` + `core/db/connection.py` |
| Vector search | Qdrant 1.15 | Collection `agent_events`, 1536-dim cosine |
| Cache | Redis 7 | Embedding cache (24 h TTL) — cache key uses `hashlib.sha256`, not `hash()` |
| Dashboard | Next.js 14 App Router | `dashboard/app/` |
| Python SDK | `zizkadb-sdk` (PyPI) | `sdk/python/zizkadb/client.py` |
| TypeScript SDK | `zizkadb-sdk` (npm) | `sdk/typescript/src/index.ts` |
| Integrations | `zizkadb-langchain`, `zizkadb-crewai`, `zizkadb-livekit` | `integrations/` (standalone) + LangChain/CrewAI bundled in `sdk/python/zizkadb/integrations/` |
| MCP server | `zizkadb-mcp` (PyPI, **MIT**) | `mcp/zizkadb_mcp/server.py` |
| Infra | Docker Compose | `infra/docker-compose.yml` (prod) + `infra/docker-compose.dev.yml` (dev overlay) |
| CI | GitHub Actions | `.github/workflows/ci.yml` (lint+tests+vitest) · `integration.yml` (weekly stack) · `publish-images.yml` (GHCR on `v*` tag) |

---

## Module map

| Directory | What it is |
|---|---|
| `core/` | FastAPI backend — all business logic, auth, DB, Qdrant, Redis |
| `dashboard/` | Next.js 14 marketing + authenticated dashboard. Source of truth: `dashboard/DASHBOARD_KNOWLEDGE_BASE.md` |
| `sdk/python/` | Async Python client + `zizkadb init` CLI |
| `sdk/typescript/` | TypeScript/JS HTTP client |
| `integrations/` | Standalone LangChain, CrewAI, and LiveKit adapter packages |
| `mcp/` | MCP server (MIT license — not AGPL) |
| `infra/` | Docker Compose files, nginx, deploy scripts |
| `scripts/` | Dev setup, release, smoke test, DB seed scripts |
| `docs/adr/` | Architecture Decision Records — explains *why* key decisions were made |
| `wiki/` | GitHub wiki source (14 pages, pushed separately) |
| `examples/` | 6 self-contained runnable example agents (+ `examples/CLAUDE.md`) |
| `worked/` | Worked debugging demos |

---

## Local dev setup

```bash
git clone https://github.com/Zizka-ai/ZizkaDB.git && cd ZizkaDB
bash scripts/setup-local.sh
```

Services after startup:

| Service | URL |
|---|---|
| API | http://localhost:8000 |
| Swagger | http://localhost:8000/swagger |
| Dashboard | http://localhost:3001 |
| Postgres | localhost:5432 |
| Qdrant | localhost:6333 |
| Redis | localhost:6379 |

Dev login: http://localhost:3001/login → "Open my dashboard" (no email needed).
Dev API key: `zizkadb_dev_local` (auto-accepted when `ENV=development`).

---

## Running tests

```bash
# Python linter (ruff binary location on this machine)
/Users/apple/Library/Python/3.14/bin/ruff check core/ sdk/python/ mcp/ integrations/

# Core API unit tests
pytest core/tests/ -m "not integration" -v

# Core API integration tests (requires running stack)
ZIZKADB_RUN_INTEGRATION=1 pytest core/tests/ -m integration -v

# Python SDK tests
pytest sdk/python/tests/ -v

# MCP server tests
pytest mcp/tests/ -v

# TypeScript SDK tests
cd sdk/typescript && npm test

# Dashboard (vitest + build gate)
cd dashboard && npm run lint && npm test && npm run build
```

---

## Key architectural decisions

> Full rationale in `docs/adr/`. Summary:

**Causal lineage** — `parent_event_id` on the `events` table + PostgreSQL recursive CTE. `db.why(event_id)` walks backward through the chain. No separate causality table.

**Dual-write** — Every event goes to Postgres (source of truth, structured queries) AND Qdrant (ANN semantic search). The `events.embedding` column in Postgres is a portability copy — not used for search but enables migrations away from Qdrant without data loss.

**Auth split** — Two FastAPI dependency functions:
- `get_tenant` (`core/api/deps.py`): accepts API keys + JWTs. Use for SDK-callable routes (`/v1/events`, `/v1/search`, `/v1/memory`, etc.)
- `require_dashboard_session` (`core/api/deps.py`): JWT only, rejects API keys. Use for any route that accesses `user_id`, manages API keys, or performs destructive ops. (There is no admin router / `require_admin` in this codebase.)
- `assert_agent_allowed(tenant, agent_id)` (`core/api/deps.py`): call at the top of any per-agent analytics route AND any SDK read scoped to one agent (`memory/context`, `memory/diff`, `events/why`) to enforce scoped-key isolation.

**Billing stub** — `billing_status_payload()` always returns `has_access: True`. No Stripe or payment provider is wired. The architecture is ready for Stripe to be added; billing routes exist but are stubs.

**Rate limiting** — `core/services/rate_limiter.py` (`RateLimiter` + `RedisStorage`/`InMemoryStorage`). OTP login, the AI `/v1/agents/{id}/suggestions` throttle, community and demo-request routes use Redis-backed limits shared across workers, falling back to in-memory (per-worker) only if Redis is unavailable (`check_rate_fail_open`). OTP uses Redis when `ENV=production` or `OTP_RATE_LIMIT_STORAGE=redis`. Background: `docs/adr/005-in-process-rate-limiting.md`.

**Health** — `/health` is liveness only (always 200). `/health/deep` returns 503 when Postgres, Redis, or (with embeddings enabled) Qdrant is down.

**Schema migrations** — Fresh install: `core/db/schema.sql` (run by Docker on first boot). Running install: `ALTER TABLE IF EXISTS` in `core/db/connection.py::init_db()` (runs on every startup). Always write idempotent DDL.

**Entitlements** — `PLAN_ENTITLEMENTS` dict in `core/services/entitlements.py` is the single source of truth for per-plan API key caps. Override per-plan at runtime with `API_KEY_LIMIT_<PLAN>=N` env var. Never hardcode plan limits anywhere else.

---

## Coding conventions

### Python (core/, sdk/python/, mcp/, integrations/)
- Async everywhere — `async def`, `await`, `asyncpg` (never SQLAlchemy)
- Connection pool from `core/db/connection.py::get_pool()` — never open connections directly
- FastAPI `Depends()` for auth — see auth split above
- Style: follow surrounding code; ruff is the linter and enforcer
- `core/api/utils.py` has shared `client_ip()` and `check_rate()` — use these, never duplicate

### TypeScript / Dashboard (dashboard/)
- Next.js 14 App Router; interactive pages are Client Components (`'use client'`)
- No React Query, SWR, Redux, Zustand, or Context — local `useState`/`useEffect` only
- All API calls go through `dashboard/lib/api.ts::apiFetch` — never raw `fetch`
- Auth split: middleware reads `access-token` cookie; client JS reads `localStorage`
- No Prettier — match surrounding style (2-space, single quotes, no semicolons)
- Wrap `useSearchParams` pages in `<Suspense>`; guard async effects with `let cancelled = false`

### TypeScript SDK (sdk/typescript/)
- Sync constructor: `new ZizkaDB({ apiKey: '...' })`
- camelCase everywhere: `parentId`, `eventId`, `sessionId` (Python SDK is snake_case)

---

## Critical invariants — never break these

1. **Auth deps**: never put `require_dashboard_session` routes on `get_tenant` — API-key holders must not be able to manage keys or access user data.
2. **Route paths**: never rename `/v1/...` paths without updating `dashboard/lib/api.ts` — there is no runtime contract enforcement between backend and dashboard.
3. **`assert_agent_allowed()`**: every per-agent analytics route and scoped SDK read (`agent_stats`, `list_sessions`, `agent_baseline`, `agent_behavior_change`, `time_travel`, `report`, `token-usage`, `token-optimization`, `suggestions`, `memory/context`, `memory/diff`, `events/why`) must call this.
4. **Entitlements single source**: plan caps live only in `core/services/entitlements.py::PLAN_ENTITLEMENTS`.
5. **Idempotent DDL**: all schema changes must use `IF NOT EXISTS` / `IF EXISTS` — the same migration runs on fresh installs and live databases.
6. **`docker-compose.yml` is production**: never add `--reload` or `../core:/app` volume mounts to the base compose file. Those belong in `docker-compose.dev.yml`.
7. **Self-hosted security**: always set `ENV=production` in production `.env` — without it, hardcoded dev API keys (`zizkadb_dev_local`) are accepted, bypassing auth entirely.
8. **`NEXT_PUBLIC_DEV_MODE`**: must be `false` in any public-facing deployment — when `true`, the dashboard login screen is bypassed entirely (no OTP required).

---

## Key cross-cutting update rules

| If you change… | Also update… |
|---|---|
| Any `/v1/` route or response shape | `dashboard/lib/api.ts` types + `dashboard/DASHBOARD_KNOWLEDGE_BASE.md` §17.3 |
| Auth / billing / signup flow | `dashboard/DASHBOARD_KNOWLEDGE_BASE.md` §18 |
| DB schema | `core/db/schema.sql` + `core/db/connection.py::init_db()` + KB §21 |
| Plan entitlements | Only `core/services/entitlements.py::PLAN_ENTITLEMENTS` |
| SDK version | Bump `sdk/python/pyproject.toml`, `sdk/typescript/package.json`, `mcp/pyproject.toml`, `core/main.py version=` together |

---

## Where to find deeper context

| Resource | Location |
|---|---|
| AI-assisted development map | `docs/ai/README.md` |
| Team coding standards (44 sections) | `docs/ai/CODING_STANDARDS.md` |
| Repo mapping for AI | `docs/ai/ZIZKADB_MAPPINGS.md` |
| Dashboard source of truth (~1,100 lines) | `dashboard/DASHBOARD_KNOWLEDGE_BASE.md` |
| Architecture decisions (why) | `docs/adr/` |
| Cursor rules (per-area conventions) | `.cursor/rules/` — **`coding-standards.mdc` and `ai-knowledge-base.mdc` always apply** |
| Agent entry (all tools) | `AGENTS.md` |
| Release workflow | `.cursor/skills/zizkadb-release/SKILL.md` |
| Wiki (14 pages) | `wiki/` or https://github.com/Zizka-ai/ZizkaDB/wiki |
| REST API explorer | http://localhost:8000/swagger |
| Self-hosting guide | `wiki/Self-Hosting.md` |
