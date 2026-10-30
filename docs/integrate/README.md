# Integrate ZizkaDB

**Already have an agent?** You only need three things:

1. A running ZizkaDB (local, self-host, or [db.zizka.ai](https://db.zizka.ai))
2. An **agent id** string (same name in code and dashboard)
3. Calls to `log()` (SDK or REST) with optional `parent_id` and `session_id`

## Choose your path

| Stack | Guide |
|-------|-------|
| **Any framework / custom code** | [any-agent.md](any-agent.md) |
| **What's officially supported** | [frameworks.md](frameworks.md) |
| **LiveKit voice agents** | [integrations/livekit.md](../integrations/livekit.md) |
| **Copy-paste quickstart** | [CONNECT.md](../../CONNECT.md) |
| **GitHub Actions (CI)** | [github-actions.md](github-actions.md): ZizkaDB Why and ZizkaDB Audit |

## Minimal Python (local)

```bash
bash scripts/setup-local.sh   # or quickstart-remote.sh
pip install zizkadb-sdk
```

```python
import asyncio
from zizkadb import ZizkaDB

async def main():
    async with ZizkaDB(host="http://localhost:8000") as db:
        turn = await db.log(agent="my-bot", event="user_message", data={"text": "Hi"})
        await db.log(agent="my-bot", event="assistant_response", data={"text": "Hello"}, parent_id=turn.event_id)

asyncio.run(main())
```

Open http://localhost:3001/login → **Open my dashboard** → **Activity** tab → select `my-bot`.

## Verify

1. Dashboard → **Activity** → agent appears in the header selector (~30s poll)
2. **Settings** → connection test (uses a separate test agent name)
3. Run `await db.why(event_id)` in the SDK to confirm causal links

See [wiki/Troubleshooting](../../wiki/Troubleshooting.md) if events do not appear.
