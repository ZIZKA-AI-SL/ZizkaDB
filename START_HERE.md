# Start here

**When your agent misbehaves, see why** — free, local, no signup.

## 1. Try it (60 seconds)

```bash
curl -fsSL https://raw.githubusercontent.com/ZIZKA-AI-SL/ZizkaDB/main/scripts/quickstart-remote.sh | bash
```

Requires Docker. You should see a tree from `tool_call` back to `user_message`.

Run again: `pip install zizkadb-sdk && zizkadb demo`

## 2. Why?

1. Log steps with `parent_id`
2. `zizkadb why <event_id>` or dashboard → event → **Why? (causal)**
3. Root cause in seconds

Dashboard: http://localhost:3001/dashboard/activity?agent=support-bot

## 3. Connect your code

```python
async with ZizkaDB(host="http://localhost:8000") as db:
    user = await db.log(agent="my-bot", event="user_message", data={"text": "Hello"})
    tool = await db.log(agent="my-bot", event="tool_call", data={"tool": "search"}, parent_id=user.event_id)
    (await db.why(tool.event_id)).print()
```

→ [CONNECT.md](CONNECT.md) for LangChain, CrewAI, **LiveKit (voice)**, TypeScript, MCP

## Next

- [worked/01-support-order-delay](worked/01-support-order-delay/) — demo story
- [examples/](examples/) — sample agents
- [README.md](README.md) — full overview

**Contributing / AI coding:** [AGENTS.md](AGENTS.md) · [docs/ai/](docs/ai/) — standards load automatically; no need to paste rules into every chat.

**Managed cloud (optional):** [db.zizka.ai/signup](https://db.zizka.ai/signup)
