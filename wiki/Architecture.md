# Architecture

ZizkaDB is a causal database for AI agents. Every event records the event that caused it (causal lineage), which powers `db.why()`, time-travel debugging with `db.at()`, session replay, and a hash-chained, tamper-evident audit log.

It is an agentic database: each event carries a SHA-256 checksum over its content and audit fields and is hash-chained per agent, so the stored record is cryptographic telemetry for AI agents. That is what makes agent auditability and EU AI Act Article 12 record-keeping possible. Agent drift measurement (`GET /v1/agents/{agent_id}/baseline`) compares recent sessions with the agent's history.

## Stack

| Layer | Technology |
|-------|------------|
| API | Python, FastAPI |
| Database | PostgreSQL + pgvector |
| Vector search | Qdrant (HNSW) |
| Cache | Redis |
| Dashboard | Next.js 14 |
| Auth | Passwordless OTP + API keys (SHA-256 hash) |

## Data model

```
tenants
  ├── users (email login)
  ├── api_keys (optional agent_id scope)
  ├── agents (agent_id per tenant)
  └── events (append-only, with embedding + parent_id)
```

### Events table

- `event_id` — UUID primary key
- `agent_id` — which agent emitted this
- `event_type` — e.g. `user_message`, `tool_call`
- `data` — JSON payload
- `parent_event_id` — causal link for `why()`
- `session_id` — group related turns
- `embedding` — vector(1536) for semantic search
- `sequence_no` — monotonic ordering

### API key scoping

| `agent_id` on key | Behavior |
|-------------------|----------|
| Set (e.g. `my-bot`) | Key only works for that agent |
| NULL (tenant-wide) | Key works for any agent name |

## Request flow (log event)

```
Client → POST /v1/events
  → verify API key (update last_used)
  → assert agent scope (403 if mismatch)
  → upsert agents row
  → insert events row
  → generate embedding
  → upsert Qdrant vector
  → return event_id
```

## Why causal chains work

Each event can set `parent_id` to the event that caused it. `GET /v1/events/{id}/why` walks the tree recursively.

## Deployment topology (managed cloud)

```
Internet
  → nginx (db.zizka.ai)
      → /v1/*  → FastAPI :8000
      → /*     → Next.js dashboard :3001
  → Postgres, Redis, Qdrant (private)
```

## Open source layout

```
ZizkaDB/
  core/           # FastAPI API
  dashboard/      # Next.js UI
  sdk/python/     # zizkadb-sdk
  sdk/typescript/ # zizkadb-sdk (npm)
  mcp/            # zizkadb-mcp
  infra/          # Docker, deploy scripts
  wiki/           # GitHub wiki source (this folder)
```

## License

- API, dashboard, Python/TS SDKs: **AGPL-3.0**
- MCP server: **MIT**
