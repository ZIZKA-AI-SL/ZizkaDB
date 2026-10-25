# core/ — ZizkaDB API

The FastAPI backend. Handles all event ingestion, causal chain queries, semantic search, agent management, authentication, and dashboard APIs.

## Stack

- **FastAPI** 0.111 + **asyncpg** (no ORM) + **Python 3.10+**
- **PostgreSQL 16** + pgvector (events, agents, users, API keys)
- **Qdrant** (vector semantic search — collection `agent_events`, 1536-dim cosine)
- **Redis 7** (embedding cache, 24 h TTL)

## Routers (15 total; all at `/v1/` except `/metrics`)

Authoritative list: `main.py` (`app.include_router`). There is **no** `admin.py` or `stats.py` router in this OSS tree.

| File | Prefix | Purpose |
|---|---|---|
| `api/auth.py` | `/v1/auth` | OTP login, JWT tokens, API key management |
| `api/events.py` | `/v1/events` | Log events (single and `/batch`), query events (cursor paging), time-travel (`db.at()`), checksum and chain verification |
| `api/sessions.py` | `/v1/sessions` | Cross-agent session timeline and session-scoped `why()` |
| `api/agents.py` | `/v1/agents` | Agent stats, sessions, causal lineage (`db.why()`), baseline, API keys |
| `api/a2a.py` | `/v1/a2a` | Agent-to-agent messages (scoped API keys) |
| `api/search.py` | `/v1/search` | Keyword, semantic and hybrid search (`db.search()`) |
| `api/memory.py` | `/v1/memory` | Memory injection (`db.context_for()`, `db.memory_diff()`) |
| `api/telemetry.py` | `/v1/telemetry` | Anonymous SDK install pings |
| `api/billing_checkout.py` | `/v1/billing` | Plan/trial status (stubbed — always `has_access: True`) |
| `api/settings.py` | `/v1/settings` | Tenant settings, embedding config |
| `api/account.py` | `/v1/account` | User profile, GDPR consent |
| `api/demo_requests.py` | `/v1/demo-requests` | Public demo request form (landing newsletter / book demo) |
| `api/community.py` | `/v1/community` | Public community board |
| `api/marketing_subscriptions.py` | `/v1/marketing-subscriptions` | Email signup |
| `api/metrics.py` | `/metrics` | Prometheus metrics; off unless `METRICS_TOKEN` is set (bearer) |

Swagger UI: `http://localhost:8000/swagger`

## Local dev

```bash
# From repo root — starts API + all services
bash scripts/setup-local.sh

# API runs on http://localhost:8000
# Dev API key: zizkadb_dev_local (auto-accepted in development mode)
```

## Tests

```bash
pytest core/tests/ -m "not integration" -v     # unit tests
pytest core/tests/ -m integration -v           # needs running stack
```

## Key files

- `main.py` — FastAPI app, lifespan, router mounts
- `api/deps.py` — Auth dependencies (`get_tenant`, `require_dashboard_session`, `assert_agent_allowed`)
- `db/connection.py` — Connection pool setup, startup migrations
- `db/schema.sql` — Complete database schema
- `services/entitlements.py` — Single source of truth for plan caps
- `services/event_write.py` — Core event ingestion pipeline

See [`CLAUDE.md`](CLAUDE.md) for full conventions and architectural context.
