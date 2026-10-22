# zizkadb-sdk

**npm:** [zizkadb-sdk 0.3.0](https://www.npmjs.com/package/zizkadb-sdk)

TypeScript SDK for [ZizkaDB](https://db.zizka.ai) — causal lineage, time travel, and semantic search for AI agents.

## Setup (managed cloud)

1. [Sign up](https://db.zizka.ai/signup) at db.zizka.ai  
2. **Dashboard → Create agent** (e.g. `my-bot`) — you get an API key for that agent  
3. Use the **same agent name** in every `db.log()` call  
4. Set `ZIZKADB_API_KEY` (or pass the key to the constructor)

> **Important:** The `agent` in `db.log({ agent: '...' })` must match the agent you created in the dashboard. A mismatch returns **403 AgentScopeError**.

## Install

```bash
npm install zizkadb-sdk
```

## Quickstart

```typescript
import { ZizkaDB } from 'zizkadb-sdk'

const db = new ZizkaDB({ apiKey: 'zizkadb_live_xxxx' })

const result = await db.log({
  agent: 'my-bot', // must match dashboard agent name
  event: 'tool_call',
  data: { tool: 'search' },
})

const chain = await db.why(result.eventId)
chain.print()
```

## Python vs TypeScript

| Feature | Python SDK | TypeScript SDK |
|---------|------------|----------------|
| `token_usage` on `log()` | `token_usage={...}` kwarg merges into event `data` | **Not supported** — pass token counts inside `data` manually, or use the Python SDK |
| `db.token_usage()` analytics | Yes | Yes (`tokenUsage()`) |

## Multi-agent apps (one key, many agent names)

If your app logs to **different agent ids** per user (e.g. `conv-alice`, `conv-bob`), create a **tenant-wide key** in **Settings → Tenant-wide API key**. Per-agent keys only work for that one agent name.

## Errors

| Error | Meaning |
|-------|---------|
| `AuthError` | Invalid or revoked API key |
| `AgentScopeError` | Key is for agent A but you logged to agent B |

## Environment variables

| Variable | Purpose |
|----------|---------|
| `ZIZKADB_API_KEY` | Your cloud API key (preferred) |
| `AGENTDB_API_KEY` | Legacy alias for `ZIZKADB_API_KEY` |
| `ZIZKADB_HOST` | Self-hosted API URL |
| `ZIZKADB_TELEMETRY` | Set `false` to opt out (anonymous ping on client construct) |

Core HTTP client only — LangChain, CrewAI, and LiveKit adapters are Python packages (`zizkadb-langchain`, `zizkadb-crewai`, `zizkadb-livekit`).

## Links

- [Docs](https://db.zizka.ai/docs)
- [API explorer](https://db.zizka.ai/swagger)
- [GitHub](https://github.com/Zizka-ai/ZizkaDB)
