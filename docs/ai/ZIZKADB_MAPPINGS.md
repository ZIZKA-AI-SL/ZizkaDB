# ZizkaDB — mapping team standards to this repo

How [CODING_STANDARDS.md](CODING_STANDARDS.md) applies to **this** codebase. Read this when a generic rule mentions patterns we do not use literally.

---

## Stack (this repo)

| Layer | Technology | Where |
|-------|------------|--------|
| Dashboard | Next.js 14 App Router, TypeScript strict | `dashboard/` |
| API | FastAPI + asyncpg (no ORM) | `core/` |
| Database | PostgreSQL 16 + pgvector | `core/db/schema.sql`, `connection.py` |
| Vector search | Qdrant | `core/services/event_write.py`, embeddings |
| Cache | Redis 7 | Embedding cache, optional OTP rate limit |
| SDKs | Python (async), TypeScript (sync) | `sdk/python/`, `sdk/typescript/` |
| Integrations | LangChain, CrewAI, LiveKit | `integrations/` |
| MCP | MIT server | `mcp/` |

---

## Feature-oriented architecture (§4) → our folders

We do **not** use a top-level `features/` directory. Map concepts as follows:

| Generic concept | ZizkaDB location |
|-----------------|------------------|
| Feature UI + routes | `dashboard/app/dashboard/<tab>/`, `dashboard/app/signup/`, marketing under `dashboard/app/` |
| Feature hooks | `dashboard/hooks/` |
| API / service layer | `dashboard/lib/api.ts` (`apiFetch` + typed functions) |
| Shared UI | `dashboard/components/ui/`, `dashboard/components/dashboard/` |
| Types | Colocated in `lib/api.ts` or feature-adjacent files |
| Backend route | `core/api/<area>.py` |
| Business logic | `core/services/<area>.py` |
| Data access | asyncpg via `get_pool()` in services — **no Repository package** |

---

## React data flow (§6, §13)

```
Component (dashboard/components/…)
    ↓
Hook (dashboard/hooks/…)
    ↓
lib/api.ts (apiFetch)
    ↓
FastAPI /v1/…
    ↓
core/services/…
    ↓
PostgreSQL / Redis / Qdrant
```

- **Never** scatter `fetch()` in components — use `apiFetch` only.
- **No** React Query, SWR, Zustand, Redux, or Context for app state (architectural choice). Use local `useState`/`useEffect`; `useAgents()` uses a small module-level pub/sub.

---

## Python backend (§15)

```
FastAPI route (core/api/*.py)
    ↓
Depends(get_tenant | require_dashboard_session)
    ↓
Service (core/services/*.py)
    ↓
asyncpg pool (core/db/connection.py::get_pool())
    ↓
PostgreSQL / Redis / Qdrant
```

- Do not put significant business logic in route handlers.
- Do not use SQLAlchemy or raw `asyncpg.connect()` — always the pool.

---

## Auth (§24) — ZizkaDB-specific

| Caller | Dependency | File |
|--------|------------|------|
| SDK / agent logging | `get_tenant` | `core/api/deps.py` |
| Dashboard management | `require_dashboard_session` (JWT only) | `core/api/deps.py` |
| Per-agent analytics + scoped SDK reads | `get_tenant` + `assert_agent_allowed` | First line of handler |

There is **no** `require_admin` or `/v1/admin` in this OSS repo.

---

## Database migrations (§16)

| Install type | Mechanism |
|--------------|-----------|
| Fresh | `core/db/schema.sql` |
| Running | `core/db/connection.py::init_db()` (idempotent `ALTER`) |
| Named SQL files | `core/db/migrations/002_user_billing.sql`, `004_tenant_embeddings.sql`, `005_agent_api_keys.sql` |

Never non-idempotent DDL on live databases.

---

## Redis (§18)

Primary uses in this repo:

- Embedding cache (24h TTL) — `core/services/embeddings.py`
- OTP rate limiting when `OTP_RATE_LIMIT_STORAGE=redis`

No general-purpose job queue in OSS (no Celery/RQ). Expensive work stays in request path or startup unless explicitly designed otherwise.

---

## API contracts (§14, §31)

- Dashboard client types: `dashboard/lib/api.ts`
- Endpoint map: `dashboard/DASHBOARD_KNOWLEDGE_BASE.md` §17.3
- Renaming `/v1/...` paths requires updating `api.ts` in the same PR

---

## Testing (§26)

| Layer | Command |
|-------|---------|
| Python lint | `ruff check core/ sdk/python/ mcp/ integrations/` |
| Core unit | `pytest core/tests/ -m "not integration" -v` |
| Dashboard | `cd dashboard && npm run lint && npm test && npm run build` |

Full matrix: `.cursor/skills/zizkadb-test/SKILL.md`

---

## GitHub issues (§37–38)

| Contributor type | Issue required? |
|------------------|-----------------|
| External / small fix (typo, obvious bug) | No — see [CONTRIBUTING.md](../../CONTRIBUTING.md) |
| Non-trivial feature, API, schema, auth | Yes — discuss or open issue first |
| Maintainers | Issue + labels — see [MAINTAINER.md](MAINTAINER.md) |

---

## Documentation sync (§34)

| If you change… | Update… |
|----------------|---------|
| `/v1/` API | `dashboard/lib/api.ts` + KB §17.3 |
| Auth / signup | KB §7, §8, §18 |
| DB schema | `schema.sql` + `init_db()` + KB §21 |
| AI / coding rules | `docs/ai/CODING_STANDARDS.md` or this file |
| What any component sends by default | `docs/telemetry.md` + CHANGELOG |
| Single-container image (`infra/allinone/`) | `redu.md` + README single-container section + `wiki/Self-Hosting.md` |
| SDK host / API-key resolution | Both SDK READMEs + `wiki/Troubleshooting.md` + templates and examples |

---

## Not applicable in OSS

- Operator admin console (`/admin`, `/v1/admin/*`) — managed-cloud only; KB sections are reference-only
- Background worker fleet — minimal; no separate worker process in default compose
- `features/` root folder — use `dashboard/app/` structure instead
