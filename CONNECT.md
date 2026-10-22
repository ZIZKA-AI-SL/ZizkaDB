# Connect your agent (self-host / OSS)

**Current PyPI releases:** `zizkadb-sdk` **0.3.0** · `zizkadb-mcp` **0.1.8** · `zizkadb-langchain` **0.1.3** · `zizkadb-crewai` **0.1.3** · `zizkadb-livekit` **0.2.1**

## Start the stack (pick one)

**No repo clone** (recommended for new users):

```bash
curl -fsSL https://raw.githubusercontent.com/Zizka-ai/ZizkaDB/main/scripts/quickstart-remote.sh | bash
```

Only ~4 small files land in `~/.zizkadb/infra/`. Pre-built images from `ghcr.io/zizka-ai-sl/`.

**From a git clone** (contributors):

```bash
git clone https://github.com/Zizka-ai/ZizkaDB.git && cd ZizkaDB
bash scripts/quickstart.sh
```

Open **http://localhost:3001/login** → **Open my dashboard →** (no signup, no API key on localhost).

Pick your stack below. Use the **same agent name** in code and dashboard.

---

## Python SDK

```bash
pip install "zizkadb-sdk>=0.3.0"
```

```python
import asyncio
from zizkadb import ZizkaDB

async def main():
    async with ZizkaDB(host="http://localhost:8000") as db:
        user = await db.log(
            agent="my-bot",
            event="user_message",
            data={"text": "Hello"},
        )
        await db.log(
            agent="my-bot",
            event="tool_call",
            data={"tool": "search"},
            parent_id=user.event_id,
        )
        print("Logged — refresh dashboard → Activity → my-bot")

asyncio.run(main())
```

Env vars (optional):

```bash
export ZIZKADB_HOST=http://localhost:8000
export ZIZKADB_AGENT=my-bot
```

---

## Name your tools and API calls

An event type like `tool_call` says *that* a tool ran. Name it and the dashboard, reports and drift detection show *which* one: `tool_call · lookup_order`, calls and error rate per tool, and "refund calls up 12pp" instead of "tool calls up".

```python
call = await db.log_tool("support-bot", "lookup_order", args={"order_id": "ORD-8842"})
await db.log_tool_result("support-bot", "lookup_order", result=order, parent_id=call.event_id)
# on failure: await db.log_tool_result("support-bot", "lookup_order", error=exc, parent_id=call.event_id)

await db.log_api("support-bot", "POST", "https://api.stripe.com/v1/refunds", status=200, duration_ms=312)
```

```typescript
const call = await db.logTool({ agent: 'support-bot', tool: 'lookup_order', args: { orderId: 'ORD-8842' } })
await db.logToolResult({ agent: 'support-bot', tool: 'lookup_order', result: order, parentId: call.eventId })

await db.logApi({ agent: 'support-bot', method: 'POST', url: 'https://api.stripe.com/v1/refunds', status: 200 })
```

`log_api` drops the query string, fragment and credentials from the URL before logging, and names the call `POST api.stripe.com/v1/refunds` unless you pass `name`. A 4xx/5xx status or an `error` counts toward that API's error rate.

Using plain `db.log()` or REST? Follow the same convention and you get the same views:

| What | Event type | Payload keys |
|------|-----------|--------------|
| Tool call | `tool_call` | `data.tool` (name), `data.args` |
| Tool outcome | `tool_result` / `tool_error` | `data.tool`, `data.result` or `data.error` |
| API call | `api_call` | `data.api` (name) or `data.method` + `data.url`; `data.status` |

The LangChain, LiveKit and MCP integrations already log tool names this way.

---

## TypeScript SDK

```bash
npm install zizkadb-sdk
```

```typescript
import { ZizkaDB } from 'zizkadb-sdk'

const db = new ZizkaDB({ host: 'http://localhost:8000' })

const user = await db.log({
  agent: 'my-bot',
  event: 'user_message',
  data: { text: 'Hello' },
})
await db.log({
  agent: 'my-bot',
  event: 'tool_call',
  data: { tool: 'search' },
  parentId: user.eventId,
})
```

---

## LangChain

```bash
pip install "zizkadb-sdk>=0.3.0" "zizkadb-langchain>=0.1.3"
```

```python
import asyncio
from langchain_openai import ChatOpenAI
from zizkadb import ZizkaDB
from zizkadb_langchain import ZizkaDBCallbackHandler

async def main():
    async with ZizkaDB(host="http://localhost:8000") as db:
        handler = ZizkaDBCallbackHandler(db=db, agent="my-bot")
        llm = ChatOpenAI(model="gpt-4o-mini")
        await llm.ainvoke("Hello", config={"callbacks": [handler]})

asyncio.run(main())
```

Or scaffold: `zizkadb init my-agent --template langchain`

---

## CrewAI

```bash
pip install "zizkadb-sdk>=0.3.0" "zizkadb-crewai>=0.1.3"
```

```python
from crewai import Agent, Crew, Task
from zizkadb import ZizkaDB
from zizkadb_crewai import ZizkaDBCrewLogger

db = ZizkaDB(host="http://localhost:8000")
logger = ZizkaDBCrewLogger(db, agent="my-bot")
# log kickoff → task → task → output, then explain the run:
# (await db.why(logger.last_event_id)).print()
# see integrations/crewai/ and examples/crewai-agent/
```

Or scaffold: `zizkadb init my-agent --template crewai`

---

## LiveKit Agents (voice)

One **LiveKit call** → one **ZizkaDB session** in Activity. Transcript text is copied from LiveKit at call end — **no audio** stored in ZizkaDB.

```bash
pip install "zizkadb-livekit>=0.2.1"
```

```python
import os
from livekit.agents import AgentServer, JobContext, AgentSession, Agent
from zizkadb import ZizkaDB
from zizkadb_livekit import ZizkaDBLiveKitObserver, pop_observer, register_observer

server = AgentServer()


async def on_session_end(ctx: JobContext) -> None:
    # One observer per call: a worker serves many calls at once.
    observer = pop_observer(ctx)
    if observer is None:
        return
    try:
        await observer.ingest_session_report(ctx)
    finally:
        await observer.aclose()


@server.rtc_session(agent_name="support-voice", on_session_end=on_session_end)
async def entrypoint(ctx: JobContext):
    db = ZizkaDB(host=os.getenv("ZIZKADB_HOST", "http://localhost:8000"))
    observer = ZizkaDBLiveKitObserver(
        db,
        agent=os.getenv("ZIZKADB_AGENT", "support-voice"),
        session_id=ctx.room.name,
    )
    register_observer(ctx, observer)

    session = AgentSession(...)  # your STT / LLM / TTS
    observer.attach(session, job_ctx=ctx)      # before session.start()
    observer.queue_session_started(room=ctx.room.name, job_id=ctx.job.id)

    await session.start(agent=Agent(instructions="..."), room=ctx.room)
    await ctx.connect()
```

After a test call: **Activity → support-voice → Sessions** — same event types as text agents (`user_message`, `assistant_response`, `tool_call`, `session_ended`).

Full sample: [examples/livekit-agent/](examples/livekit-agent/) (voice worker + local browser UI at `http://localhost:8080`) · package docs: [integrations/livekit/](integrations/livekit/)

---

## MCP (Cursor, Claude Desktop, Windsurf)

```bash
pip install "zizkadb-mcp>=0.1.8"
# or: uvx zizkadb-mcp
```

`~/.cursor/mcp.json` (or Claude Desktop config):

```json
{
  "mcpServers": {
    "zizkadb": {
      "command": "uvx",
      "args": ["zizkadb-mcp"],
      "env": {
        "ZIZKADB_HOST": "http://localhost:8000"
      }
    }
  }
}
```

On localhost the dev key is auto-injected. Reload MCP in your editor.

Or scaffold: `zizkadb init my-agent --template mcp-cursor`

Full guide: [mcp/README.md](mcp/README.md)

---

## REST (any language)

```bash
curl -s -H "Authorization: Bearer zizkadb_dev_local" \
  -H "Content-Type: application/json" \
  -d '{"agent":"my-bot","event":"started","data":{"ok":true}}' \
  http://localhost:8000/v1/events
```

Swagger: http://localhost:8000/swagger

---

## Verify

1. Dashboard → **Activity** — `my-bot` appears within ~30s  
2. Settings → **Send test event** (logs to `dashboard-connection-test`, not your agent)  
3. Settings with `?agent=my-bot` → **Test agent**

---

## Production self-host

Local dev uses the auto dev key. For a VPS:

1. In `infra/.env` set `ENV=production`, `DEPLOYMENT_MODE=self_hosted` and `SELFHOST_ADMIN_TOKEN=<long-random>` (no email/SMTP needed)
2. `docker compose -f infra/docker-compose.yml up -d`
3. `bash infra/deploy-selfhost.sh`
4. Open the dashboard → enter the admin token → create API keys in Settings → use them in SDK/MCP

See [wiki/Self-Hosting](https://github.com/Zizka-ai/ZizkaDB/wiki/Self-Hosting) and [Production Deployment](https://github.com/Zizka-ai/ZizkaDB/wiki/Production-Deployment).

---

## Managed cloud

If you prefer hosted ZizkaDB instead of Docker: [db.zizka.ai/signup](https://db.zizka.ai/signup) — same SDK, use `zizkadb_live_...` keys instead of `host=`.
