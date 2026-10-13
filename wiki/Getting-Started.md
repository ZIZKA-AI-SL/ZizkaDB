# Getting Started

**Open source (recommended):** run ZizkaDB locally — **no full repo download required.**

## Fastest path — no git clone (~60 seconds)

```bash
curl -fsSL https://raw.githubusercontent.com/Zizka-ai/ZizkaDB/main/scripts/quickstart-remote.sh | bash
```

Downloads only compose + schema to `~/.zizkadb`, pulls pre-built images, runs `zizkadb demo`.

| Service | URL |
|---------|-----|
| Dashboard | http://localhost:3001/login → **Open my dashboard →** |
| API | http://localhost:8000/health |

## Have the repo? One-command quickstart

```bash
git clone https://github.com/Zizka-ai/ZizkaDB.git && cd ZizkaDB
bash scripts/quickstart.sh
```

### Run the demo again

```bash
pip install zizkadb-sdk
zizkadb demo
```

Worked example: [worked/01-support-order-delay](../worked/01-support-order-delay/)

### Connect your own agent

See **[CONNECT.md](../CONNECT.md)** — Python, TypeScript, LangChain, CrewAI, **LiveKit (voice)**, MCP, REST.

### Stack only (no demo)

```bash
bash scripts/setup-local.sh
```

---

## OSS ladder

1. **Taste lineage** — `bash scripts/quickstart.sh` or `zizkadb demo`
2. **Connect your code** — [CONNECT.md](../CONNECT.md) or `zizkadb init my-agent --template basic`
3. **Go deeper** — [examples/](../examples/), [integrations/](../integrations/)
4. **Production** — [[Self-Hosting]], [[Production-Deployment]]

---

## Golden rule (self-host)

> Use the **same tenant** in SDK and dashboard.
>
> **Local dev:** login → *Open my dashboard →* + `ZizkaDB(host="http://localhost:8000")`
>
> **Production VPS:** admin token (`SELFHOST_ADMIN_TOKEN`) → API key in Settings → paste into SDK/MCP

---

## Managed cloud (optional)

If you prefer hosted ZizkaDB instead of Docker:

### 1. Sign up

Go to [db.zizka.ai/signup](https://db.zizka.ai/signup) → enter email → verify OTP code.

### 2. Create an agent + API key

1. Open [Dashboard → Fleet](https://db.zizka.ai/dashboard/fleet) (managed cloud)
2. Click **Create agent**, enter an id (e.g. `support-bot`)
3. **Copy the full API key** — shown once only (`zizkadb_live_...`)

On ZizkaDB Cloud, an alternative is **Settings → Tenant-wide API key** if your app uses dynamic agent names per session (see [[Multi-Agent Apps]]). Self-hosted runs one agent with one API key: use `session_id`, not extra agent names, to separate users.

### 3. Configure your environment

```bash
export ZIZKADB_API_KEY=zizkadb_live_...
export ZIZKADB_AGENT=support-bot
```

### 4. Log your first event

```bash
pip install zizkadb-sdk
```

```python
import asyncio
from zizkadb import ZizkaDB

async def main():
    async with ZizkaDB() as db:
        result = await db.log(
            agent="support-bot",
            event="connection_test",
            data={"ok": True},
        )
        print(result.event_id)

asyncio.run(main())
```

### 5. Verify in dashboard

Dashboard → **Activity** — select `support-bot` to see events.

---

## Golden rule (managed cloud)

> **The `agent` name in your code must match the agent you created in the dashboard** (unless you use a tenant-wide key on ZizkaDB Cloud — see [[Multi-Agent Apps]]). Self-hosted allows a single agent name.
