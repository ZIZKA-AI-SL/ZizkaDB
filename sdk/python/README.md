# zizkadb-sdk

**PyPI:** [zizkadb-sdk 0.4.1](https://pypi.org/project/zizkadb-sdk/0.4.1/)

Python SDK for [ZizkaDB](https://github.com/ZIZKA-AI-SL/ZizkaDB) — **Why did your agent do that?** Call `db.why(event_id)` or `zizkadb why <id>` after logging with `parent_id`.

## Setup (managed cloud)

1. [Sign up](https://db.zizka.ai/signup) at db.zizka.ai  
2. **Dashboard → Create agent** (e.g. `my-bot`) — you get an API key for that agent  
3. Use the **same agent name** in every `db.log()` call  
4. Set `ZIZKADB_API_KEY` (or pass the key to the constructor)

> **Important:** The `agent` in `db.log(agent="...", ...)` must match the agent you created in the dashboard. A mismatch returns **403 AgentScopeError**.

## Install

```bash
pip install zizkadb-sdk
```

> **Note:** There is an unrelated package called `agentdb` on PyPI. Install **`zizkadb-sdk`**. Import: `from zizkadb import ZizkaDB`.

## Scaffold a project

```bash
pip install zizkadb-sdk
zizkadb init my-agent --template basic
cd my-agent && cp .env.example .env   # paste your key into ZIZKADB_API_KEY
pip install -r requirements.txt
python agent.py
```

Templates: `basic`, `openai`, `langchain`, `crewai`, `mcp-cursor`.

## Framework packages (optional)

| Package | Install | Use case |
|---------|---------|----------|
| `zizkadb-langchain` | `pip install zizkadb-langchain` | LangChain callbacks |
| `zizkadb-crewai` | `pip install zizkadb-crewai` | CrewAI crew logger |
| `zizkadb-livekit` | `pip install zizkadb-livekit` | LiveKit voice → Sessions + Events |

See [integrations/](../integrations/) and [docs/integrations/livekit.md](../docs/integrations/livekit.md).

## Quickstart

```python
from zizkadb import ZizkaDB

# Key from Dashboard → Settings → API keys → create "my-bot" → copy key
db = ZizkaDB("zizkadb_live_xxxx")

async with db:
    result = await db.log(
        agent="my-bot",  # must match dashboard agent name
        event="tool_call",
        data={"tool": "search", "query": "billing"},
    )
    chain = await db.why(result.event_id)
    chain.print()
```

## Multi-agent apps (ZizkaDB Cloud)

An API key works for one agent name. Running several agents — and agent-to-agent messaging between them — is a [ZizkaDB Cloud](https://db.zizka.ai) feature; create each agent and its key in the dashboard.

Self-hosted ZizkaDB is single-agent: log everything under one agent name and use `session_id` to separate users or conversations. A second agent name is rejected with `409 agent_limit_reached`.

## More than logging

| Method | What it does |
|---|---|
| `log(..., idempotency_key=, supersedes=, importance=)` | Safe retries; replace an outdated fact; weight it in memory |
| `log_batch(events)` | Up to 500 events in one request |
| `search(query, mode=)` | Keyword out of the box, hybrid with embeddings |
| `context_for(agent, task)` | Relevant past as a prompt block (no OpenAI key needed) |
| `verify_chain(agent, after_seq=, anchor=)` | Prove no event was edited, removed or reordered |
| `iter_events(agent)` | Every event, cursor-paged |
| `export_events(agent)` / `import_events(lines)` | Portable NDJSON copy and import |

Needs a server from release 0.5.0. Full guide: [CONNECT.md](https://github.com/ZIZKA-AI-SL/ZizkaDB/blob/main/CONNECT.md#beyond-log-and-why).

## Errors

| Exception | Meaning |
|-----------|---------|
| `AuthError` | Invalid or revoked API key |
| `AgentScopeError` | Key is for agent A but you logged to agent B |
| `NotFoundError` | Event or agent not found |

## Environment variables

| Variable | Purpose |
|----------|---------|
| `ZIZKADB_API_KEY` | Your cloud API key (preferred) |
| `AGENTDB_API_KEY` | Legacy alias for `ZIZKADB_API_KEY` |
| `ZIZKADB_AGENT` | Default agent name (used in templates/examples) |
| `ZIZKADB_HOST` | Self-hosted API URL. Used when the client gets no `host` argument, so with an API key it sends requests here instead of ZizkaDB Cloud (legacy alias `AGENTDB_HOST`) |
| `ZIZKADB_TELEMETRY` | Set `false` to opt out (anonymous ping on client construct) |

## Self-host dashboard

1. `bash scripts/setup-local.sh` (API + dashboard on port **3001**)
2. Open http://localhost:3001/login → **Open my dashboard →**

## Links

- [Docs](https://db.zizka.ai/docs)
- [API explorer](https://db.zizka.ai/swagger)
- [GitHub](https://github.com/ZIZKA-AI-SL/ZizkaDB)
