# Python SDK

**Package:** `zizkadb-sdk` on PyPI (**0.3.0**)  
**Import:** `from zizkadb import ZizkaDB`

```bash
pip install zizkadb-sdk
```

## Quickstart

```python
import asyncio
from zizkadb import ZizkaDB

async def main():
    async with ZizkaDB("zizkadb_live_...") as db:
        user = await db.log(
            agent="my-bot",
            event="user_message",
            data={"text": "Why is my bill high?"},
            session_id="sess_001",
        )
        tool = await db.log(
            agent="my-bot",
            event="tool_call",
            data={"tool": "get_billing", "user_id": 123},
            parent_id=user.event_id,
            session_id="sess_001",
        )
        chain = await db.why(tool.event_id)
        chain.print()

asyncio.run(main())
```

## Scaffold a project

```bash
zizkadb init my-agent --template basic
zizkadb init my-agent --template langchain
zizkadb init my-agent --template crewai
zizkadb init my-agent --template openai
zizkadb init my-agent --template mcp-cursor
```

## Main methods

| Method | Description |
|--------|-------------|
| `log(agent, event, data, ...)` | Append event (embeds when tenant embeddings are configured) |
| `query(agent, ...)` | List events |
| `why(event_id)` | Causal chain via `parent_id` |
| `search(query, agent?)` | Semantic search |
| `at(agent, timestamp)` | Time-travel state |
| `context_for(agent, task)` | Prompt-ready memory block |
| `forget(filter_key, filter_value)` | GDPR delete |
| `baseline(agent)` | Behavioral drift signal |
| `agents()` | List agents |

## Errors

```python
from zizkadb import AuthError, AgentScopeError, NotFoundError

try:
    await db.log(agent="wrong-name", event="x", data={})
except AgentScopeError as e:
    print(e)  # Key scoped to different agent
except AuthError:
    print("Invalid API key")
```

## Self-hosted

```python
db = ZizkaDB(host="http://localhost:8000")
```

Localhost auto-uses dev key `zizkadb_dev_local`.

## LiveKit voice agents

```bash
pip install zizkadb-livekit
```

One call → one session in Activity. Guide: [docs/integrations/livekit.md](../docs/integrations/livekit.md) · example: [examples/livekit-agent/](../examples/livekit-agent/).

## Telemetry opt-out

```bash
export ZIZKADB_TELEMETRY=false
```
