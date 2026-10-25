# TypeScript SDK

**Package:** `zizkadb-sdk` on npm (**0.5.0**)

```bash
npm install zizkadb-sdk
```

## Quickstart

```typescript
import { ZizkaDB } from 'zizkadb-sdk'

const db = new ZizkaDB({ apiKey: process.env.ZIZKADB_API_KEY! })

const result = await db.log({
  agent: 'my-bot',
  event: 'tool_call',
  data: { tool: 'search' },
})

const chain = await db.why(result.eventId)
chain.print()
```

## Environment variables

| Variable | Purpose |
|----------|---------|
| `ZIZKADB_API_KEY` | Cloud API key |
| `AGENTDB_API_KEY` | Legacy alias |
| `ZIZKADB_HOST` | Self-hosted API URL. Used when the client gets no `host` argument, so with an API key it sends requests here instead of ZizkaDB Cloud (legacy alias `AGENTDB_HOST`) |
| `ZIZKADB_TELEMETRY` | Set `false` to opt out of the anonymous install ping (see [docs/telemetry.md](https://github.com/ZIZKA-AI-SL/ZizkaDB/blob/main/docs/telemetry.md)) |

Core HTTP client only — LangChain, CrewAI, and LiveKit adapters are Python packages (`zizkadb-langchain`, `zizkadb-crewai`, `zizkadb-livekit`).

## Self-hosted

```typescript
const db = new ZizkaDB({ host: 'http://localhost:8000' })
```

## Errors

```typescript
import { AuthError, AgentScopeError, ZizkaDBError } from 'zizkadb-sdk'
```

- `AuthError` — 401 invalid key
- `AgentScopeError` — 403 agent name mismatch
- `NotFoundError` — 404

## Next.js note

Server-side env vars are baked at `npm run build`. After changing `.env`, rebuild before restarting PM2.
