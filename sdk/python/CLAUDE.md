# sdk/python/ — Python SDK

See root [`CLAUDE.md`](../../CLAUDE.md) for full project context.

Package: **`zizkadb-sdk`** on PyPI. Import: `from zizkadb import ZizkaDB`.

---

## Client pattern

```python
import asyncio
from zizkadb import ZizkaDB

async def main():
    async with ZizkaDB(api_key="zizkadb_live_...") as db:
        # or: async with ZizkaDB(host="http://localhost:8000") as db:
        event = await db.log(agent="my-bot", event="user_message", data={"text": "Hello"})
        chain = await db.why(event.event_id)
        chain.print()

asyncio.run(main())
```

**There is no sync constructor.** Always use `async with ZizkaDB(...) as db:`.

---

## Core methods

| Method | What it does | Requires OpenAI key? |
|---|---|---|
| `db.verify_chain(agent, after_seq?, anchor?)` | Check the audit hash chain | No |
| `db.log(agent, event, data, parent_id?, session_id?, idempotency_key?)` | Log an event (same key on retry = stored once) | No |
| `db.why(event_id)` | Causal chain backward from an event | No |
| `db.at(agent, timestamp)` | Time-travel — agent state at a point in time | No |
| `db.query(agent, ...)` | Fetch events with filters | No |
| `db.search(query, agent?, mode?)` | Search event history: hybrid with embeddings, keyword (Postgres full-text) without | No |
| `db.context_for(agent, task)` | Relevant past events as a prompt block (keyword relevance without embeddings) | No |
| `db.memory_diff(agent)` | Compare this session to previous sessions | Yes |
| `db.baseline(agent)` | Behavioral baseline + drift signal | No |
| `db.token_usage(agent, ...)` | Token usage report for date range | No |
| `db.token_optimization(agent, ...)` | Token optimization recommendations | No |
| `db.forget(key, value)` | GDPR delete — remove events matching a value | No |
| `db.agents()` | List all agents in the project | No |

---

## Error types (`zizkadb/exceptions.py`)

- `AuthError` — invalid or revoked API key
- `AgentScopeError` — API key is scoped to a different agent (HTTP 403)
- `NotFoundError` — event or agent does not exist

---

## CLI

```bash
zizkadb init my-agent --template basic
# Templates: basic | openai | langchain | crewai | mcp-cursor

zizkadb why <event_id> [--depth N]
zizkadb baseline <agent> [--window 24h|7d|30d]
zizkadb token-usage <agent> --from YYYY-MM-DD --to YYYY-MM-DD
zizkadb token-opt <agent> --from YYYY-MM-DD --to YYYY-MM-DD
```

Templates live in `zizkadb/templates/`. Each is a self-contained directory with `.env.example`, `agent.py`, `requirements.txt`, and `README.md`.

---

## Integration code location

Integration source lives in **two places** — keep them in sync:
- `zizkadb/integrations/langchain/callbacks.py` — bundled in the SDK
- `integrations/langchain/zizkadb_langchain/callbacks.py` — standalone package

Same duplication for CrewAI. When editing integration logic, update both.

---

## Environment variables

| Variable | Purpose |
|---|---|
| `ZIZKADB_API_KEY` | API key for Zizka Cloud |
| `ZIZKADB_HOST` | URL for self-hosted or local instance; used when no `host` is passed, so it wins over Cloud for an API key |
| `ZIZKADB_AGENT` | Default agent name (used in templates) |
| `ZIZKADB_TELEMETRY` | Set `false` to opt out of anonymous usage pings |

---

## Tests

```bash
pytest sdk/python/tests/ -v
```
