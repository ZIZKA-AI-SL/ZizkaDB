# Integrate ZizkaDB with any AI agent

Framework-agnostic guide. Works with Python, TypeScript, Node, Go, or any HTTP client.

## Mental model

```
Your agent (LLM, tools, workflow)
        ↓  log each meaningful step
ZizkaDB SDK or POST /v1/events
        ↓
Postgres (source of truth) + Qdrant (semantic search)
        ↓
Dashboard: Activity · Behavior · Reports · Suggestions
```

ZizkaDB is **not** your LLM or orchestrator. It is the **operational record** of what the agent did and why.

---

## What to log

Log **decisions and actions**, not every token.

| Event type (string) | When | Example `data` |
|---------------------|------|----------------|
| `user_message` | User input received | `{"text": "..."}` |
| `assistant_response` | Model reply | `{"text": "..."}` |
| `tool_call` | Before/after tool execution | `{"tool": "search", "args": {...}}` |
| `tool_result` | Tool output | `{"result": {...}}` |
| `error` | Failure you want to debug later | `{"message": "...", "code": "..."}` |
| `decision` | Routing / policy choice | `{"route": "billing", "reason": "..."}` |

Use consistent `event` type strings within your agent so baselines and reports stay meaningful.

---

## Required fields

Every write includes:

| Field | Purpose |
|-------|---------|
| `agent` | Stable agent id — **must match** dashboard agent name (or use a tenant-wide key on ZizkaDB Cloud). Self-hosted: one agent name only |
| `event` | Type string (see above) |
| `data` | JSON payload (your schema) |

Strongly recommended:

| Field | Purpose |
|-------|---------|
| `parent_id` | UUID of the causing event → powers `why()` |
| `session_id` | Groups one conversation/run → sessions, baselines, reports |

---

## Causal lineage (`parent_id`)

Link child → parent so `db.why(event_id)` walks backward:

```python
user = await db.log(agent="bot", event="user_message", data={"text": q}, session_id=sid)
plan = await db.log(agent="bot", event="decision", data={"action": "search"}, parent_id=user.event_id, session_id=sid)
tool = await db.log(agent="bot", event="tool_call", data={"tool": "search"}, parent_id=plan.event_id, session_id=sid)
await db.why(tool.event_id)  # tool → plan → user
```

---

## Authentication

| Environment | Auth |
|-------------|------|
| Local dev (`localhost:8000`) | Dev key auto-injected: `zizkadb_dev_local` |
| Self-host production | API key from dashboard Settings |
| Managed cloud | `zizkadb_live_...` from dashboard |

Set `ZIZKADB_HOST` when not using cloud default. Set `ZIZKADB_API_KEY` for non-localhost.

**403 agent mismatch:** per-agent keys only work for that agent name. On ZizkaDB Cloud, multi-user SaaS apps often need a **tenant-wide key** (Settings) and per-user agent ids like `conv-{userId}`.

**409 agent_limit_reached (self-hosted):** self-hosted ZizkaDB runs one agent with one API key. Use the agent name in the error message and separate users with `session_id`; multiple agents need ZizkaDB Cloud.

---

## REST (any language)

```bash
curl -s -H "Authorization: Bearer $ZIZKADB_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"agent":"my-bot","event":"started","data":{"ok":true},"session_id":"run-1"}' \
  https://db.zizka.ai/v1/events
```

See [wiki/REST-API.md](../../wiki/REST-API.md) and Swagger.

---

## Errors and retries

- Log **`error`** events with `parent_id` pointing at the step that failed.
- Retries: either new events with a new `session_id` or same session with `parent_id` linking to the failed attempt — pick one convention per agent and stay consistent.
- SDK raises `AuthError`, `AgentScopeError`, `NotFoundError` — see [wiki/Troubleshooting](../../wiki/Troubleshooting.md).

---

## Production checklist

- [ ] Same `agent` string in code and dashboard
- [ ] `session_id` on every event in a conversation
- [ ] `parent_id` on tool calls and responses
- [ ] `ENV=production` + no dev keys on public deployments
- [ ] Embeddings configured if you need search / `context_for()` (Settings → Embeddings)

---

## Next steps

- [frameworks.md](frameworks.md) — LangChain, CrewAI, LangGraph, etc.
- [CONNECT.md](../../CONNECT.md) — full SDK examples
- [examples/](../../examples/) — runnable agents
