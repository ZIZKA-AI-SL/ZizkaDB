# zizkadb-sdk

**npm:** [zizkadb-sdk 0.4.1](https://www.npmjs.com/package/zizkadb-sdk)

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

## More than logging

| Method | What it does |
|---|---|
| `log({ ..., idempotencyKey, supersedes, importance })` | Safe retries; replace an outdated fact; weight it in memory |
| `logBatch(events)` | Up to 500 events in one request |
| `search({ query, mode })` | Keyword out of the box, hybrid with embeddings |
| `verifyChain(agent, after?)` | Prove no event was edited, removed or reordered |
| `iterEvents(agent)` | Every event, cursor-paged (`for await`) |
| `exportEvents(agent)` / `importEvents(lines)` | Portable NDJSON copy and import |

Needs a server from release 0.5.0. Full guide: [CONNECT.md](https://github.com/ZIZKA-AI-SL/ZizkaDB/blob/main/CONNECT.md#beyond-log-and-why).

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
| `ZIZKADB_HOST` | Self-hosted API URL. Used when the client gets no `host` argument, so with an API key it sends requests here instead of ZizkaDB Cloud (legacy alias `AGENTDB_HOST`) |
| `ZIZKADB_TELEMETRY` | Set `false` to opt out (anonymous ping on client construct) |

Core HTTP client only — LangChain, CrewAI, and LiveKit adapters are Python packages (`zizkadb-langchain`, `zizkadb-crewai`, `zizkadb-livekit`).

## Links

- [Docs](https://db.zizka.ai/docs)
- [API explorer](https://db.zizka.ai/swagger)
- [GitHub](https://github.com/ZIZKA-AI-SL/ZizkaDB)
