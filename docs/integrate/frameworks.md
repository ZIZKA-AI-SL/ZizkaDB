# Framework integration status

Only list **SUPPORTED** when an adapter or official example exists in this repository.

| Framework | Status | Package (version) |
|-----------|--------|-------------------|
| **Python (custom)** | Supported | `zizkadb-sdk` **0.3.0** — [any-agent.md](any-agent.md) |
| **TypeScript / Node** | Supported | `zizkadb-sdk` **0.3.0** (npm) — [CONNECT.md](../../CONNECT.md#typescript-sdk) |
| **REST / any HTTP client** | Supported | `POST /v1/events` — [wiki/REST-API](../../wiki/REST-API.md) |
| **LangChain (Python)** | Supported | `zizkadb-langchain` **0.1.3** — [examples/langchain-agent](../../examples/langchain-agent/) |
| **CrewAI (Python)** | Supported | `zizkadb-crewai` **0.1.3** + auto `ZizkaDBCrewAIListener` — [examples/crewai-agent](../../examples/crewai-agent/) |
| **LiveKit Agents (voice)** | Supported | `zizkadb-livekit` **0.2.0** — [docs/integrations/livekit.md](../integrations/livekit.md) · [examples/livekit-agent](../../examples/livekit-agent/) |
| **MCP (Cursor, Claude Desktop)** | Supported | `zizkadb-mcp` **0.1.8** · [mcp/README.md](../../mcp/README.md) |
| **OpenAI / Anthropic SDKs** | Example pattern | Manual `log()` around API calls · [examples/openai-agent](../../examples/openai-agent/) |
| **LangGraph** | Supported | `zizkadb-langgraph` **0.1.0** — [integrations/langgraph](../../integrations/langgraph/) |
| **LlamaIndex** | Generic only | Log at retrieval/tool boundaries via SDK or REST |
| **AutoGen** | Generic only | Log agent messages and tool calls via SDK or REST |
| **OpenAI Agents SDK** | Generic only | Wrap tool/run lifecycle with `log()` + `parent_id` |

## LangGraph (generic pattern)

There is no `zizkadb-langgraph` package. In each node:

```python
from zizkadb import ZizkaDB

async with ZizkaDB(api_key="...") as db:
    state_event = await db.log(
        agent="my-graph",
        event="graph_node",
        data={"node": "research", "input": state},
        session_id=thread_id,
        parent_id=last_event_id,  # from prior node if tracked
    )
    # ... run node logic ...
    await db.log(agent="my-graph", event="graph_node_done", data={"output": out}, parent_id=state_event.event_id, session_id=thread_id)
```

Track `last_event_id` in graph state if you need full chains across nodes.

## Adding official support

Open an issue or PR under `integrations/` — see [CONTRIBUTING.md](../../CONTRIBUTING.md).
