# core/ — FastAPI Backend

See root [`CLAUDE.md`](../CLAUDE.md) for full project context. This file covers `core/`-specific conventions.

---

## Router map

All routers are mounted in `main.py` at `/v1/<prefix>`:

The thirteen routers mounted in `main.py` (`app.include_router`):

| File | Prefix | Auth type |
|---|---|---|
| `api/auth.py` | `/v1/auth` | mixed (public OTP + JWT-only key mgmt) |
| `api/events.py` | `/v1/events` | `get_tenant` (+ `assert_agent_allowed` on `/why`, `/at`) |
| `api/agents.py` | `/v1/agents` | mixed (see below) |
| `api/a2a.py` | `/v1/a2a` | `get_tenant` |
| `api/search.py` | `/v1/search` | `get_tenant` + `assert_agent_allowed` |
| `api/memory.py` | `/v1/memory` | `get_tenant` + `assert_agent_allowed` (agent-scoped) |
| `api/telemetry.py` | `/v1/telemetry` | `get_tenant` |
| `api/billing_checkout.py` | `/v1/billing` | `require_dashboard_session` |
| `api/settings.py` | `/v1/settings` | `require_dashboard_session` |
| `api/account.py` | `/v1/account` | `require_dashboard_session` |
| `api/demo_requests.py` | `/v1/demo-requests` | public (honeypot + rate limit) |
| `api/community.py` | `/v1/community` | public (honeypot on writes) |
| `api/marketing_subscriptions.py` | `/v1/marketing-subscriptions` | public (honeypot on writes) |

Under `/v1/agents`: per-agent analytics (`/stats`, `/sessions`, `/baseline`, `/behavior-change`,
`/report`, `/token-usage`, `/token-optimization`, `/suggestions`, `/at`) use `get_tenant` +
`assert_agent_allowed`; agent + API-key management (`create_agent`, `delete_agent`, and **all**
`/api-keys` list/create/revoke) use `require_dashboard_session`.

Swagger UI: `http://localhost:8000/swagger` · OpenAPI schema: `/openapi.json`

### A2A (`POST /v1/a2a/messages`)

SDK-only. Auth is `get_tenant`, but a **tenant-wide key or JWT without `agent_id` is 403** — the sender is never a request field.

| Rule | Result |
|---|---|
| Sender | `tenant["agent_id"]` from an agent-scoped API key |
| Extra JSON (`sender_agent`, `from`, …) | Ignored; does not change sender |
| Recipient | Must exist in the **same** `tenant_id` (`agents` lookup binds both) |
| Unknown / other-tenant recipient | **404**, no `write_event` |
| Self-send (`sender == recipient`) | **400** |

Coverage: `core/tests/test_a2a.py`.

---

## Auth dependency decision tree

```
Is this route SDK-callable (agents logging events, querying data)?
  → Depends(get_tenant)            accepts API keys + JWTs

Is this route dashboard-only (manages keys, billing, user settings, destructive ops)?
  → Depends(require_dashboard_session)   JWT only, rejects API keys

Is this route per-agent analytics OR an SDK read scoped to one agent
(stats, sessions, baseline, behavior-change, time-travel, report, token-usage,
token-optimization, suggestions, memory/context, memory/diff, events/why)?
  → Depends(get_tenant)  AND  assert_agent_allowed(tenant, agent_id)  at the top of the handler
    (for memory/diff & events/why the agent is resolved from the row, then asserted;
     memory/forget constrains its delete to the scoped agent when the key is scoped)

Could an API key with no agent yet (is_unbound_api_key) reach tenant-wide data here?
  → require an explicit agent (assert_agent_allowed binds it) or return nothing.
    Never call assert_agent_allowed on arbitrary result rows — that binds the key.
```

Both dependency functions are in `api/deps.py`. `require_dashboard_session` is the pre-built instance
of `dashboard_session_dependency()`. There is no admin router in this codebase.

---

## Database pattern

```python
from db.connection import get_pool

pool = get_pool()  # asyncpg.Pool — never create your own connection

async with pool.acquire() as conn:
    row = await conn.fetchrow("SELECT ...", arg1, arg2)

# Transactions (for multi-statement atomic ops):
async with pool.acquire() as conn:
    async with conn.transaction():
        await conn.execute("UPDATE ...")
        await conn.execute("DELETE ...")
```

Never use SQLAlchemy, never open a direct `asyncpg.connect()` — always use the pool.

---

## Shared utilities

`api/utils.py` — use these, never reimplement:
- `client_ip(request)` — extracts real client IP from `X-Forwarded-For` or `request.client`
- `check_rate(key, store, limit, window_seconds)` — in-process rate limiter; `store` is a module-level dict

---

## Services map

| Service | Responsibility |
|---|---|
| `services/auth.py` | OTP generation, JWT sign/verify, API key hashing, tenant upsert |
| `services/api_keys.py` | API key CRUD, scoped-key creation, plan slot assertion |
| `services/billing.py` | Billing status stub — always returns `has_access: True` |
| `services/entitlements.py` | **Single source of truth** for plan caps (`PLAN_ENTITLEMENTS` dict) |
| `services/embeddings.py` | OpenAI embedding generation + Redis cache (24 h TTL) |
| `services/embedding_config.py` | Per-tenant embedding model config (`QDRANT_COLLECTION`, `VECTOR_SIZE`) |
| `services/event_write.py` | Core event ingestion: Postgres insert + Qdrant upsert (non-fatal if Qdrant fails) |
| `services/account.py` | Trial activation, GDPR consent, account deletion |
| `services/pricing.py` | Hardcoded per-model $/1K token pricing (`MODEL_PRICING`) + `cost_for()` — see ADR-006 |
| `services/token_usage.py` | Per-agent token/cost aggregation from `events.data.token_usage` — see ADR-006 |
| `services/token_optimization_config.py` | Tunable thresholds for the deterministic Token Optimization detectors |
| `services/token_optimization_models.py` | `TokenOptimizationSuggestion`/`Aggregates`/`Result` dataclasses (unrelated to `services/suggestions/`'s types) |
| `services/token_optimization.py` | 5 deterministic (no-LLM) detectors + orchestrator — see ADR-007 |

---

## Schema migrations

- **Fresh install**: `core/db/schema.sql` — mounted as Docker init script, runs once
- **Running install**: `core/db/connection.py::init_db()` — `ALTER TABLE IF EXISTS` runs on every startup
- **Named migrations**: `core/db/migrations/002_user_billing.sql`, `004_tenant_embeddings.sql`, `005_agent_api_keys.sql` — applied manually during deploys; additional schema in `connection.py::init_db()`
- **Rule**: always write idempotent DDL (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`)

---

## Startup behaviour

`main.py` lifespan calls `init_db()` on startup which:
1. Creates asyncpg pool + Redis + Qdrant client
2. Runs `ALTER TABLE` migrations idempotently
3. Creates community/SDK/marketing tables if missing
4. Backfills `users.plan = 'pro'` for users with `plan IS NULL`
5. Seeds a dev tenant (and logs a `DEV MODE` warning) only when `ENV` is `development` or unset — same gate as dev-key acceptance (`api.deps.dev_keys_enabled()`)

---

## Known issues (audit findings — not yet fixed)

| Issue | Location | Impact |
|---|---|---|
| `GET /health` is liveness-only, not dependency status | `main.py:109-111` | Always returns HTTP 200 once the process is up; by design it's a fast readiness probe, not a Postgres/Redis/Qdrant check. Use `/health/deep` for real dependency status. `scripts/setup-local.sh` and `scripts/quickstart-remote.sh` poll `/health` only to know the container is up; `scripts/smoke-test.sh` and `scripts/validate-selfhost-config.sh` correctly check both |
| Rate limiting 4× effective limit | `api/utils.py` in-process helpers | Non-OTP in-process limiters (if wired) still multiply by workers. OTP uses Redis when `ENV=production` or `OTP_RATE_LIMIT_STORAGE=redis` |
| 80 Postgres connections at full load | `db/connection.py` pool × workers | `max_size=20` × 4 production workers = 80 vs typical `max_connections=100`. `init_db()` logs a warning. Local/dev (not `ENV=production`) assumes 1 worker so the warning stays off. Set `WEB_CONCURRENCY` to match `--workers`. Add PgBouncer before horizontal scaling |

---

## Test commands

```bash
# Lint (ruff binary on this machine)
/Users/apple/Library/Python/3.14/bin/ruff check core/

# Unit tests
pytest core/tests/ -m "not integration" -v

# Integration tests (requires running stack)
bash scripts/setup-local.sh
ZIZKADB_RUN_INTEGRATION=1 pytest core/tests/ -m integration -v
```
