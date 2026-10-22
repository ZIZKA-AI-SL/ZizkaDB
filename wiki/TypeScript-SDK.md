# TypeScript SDK

**Package:** `zizkadb-sdk` on npm (**0.3.0**)

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
| `ZIZKADB_HOST` | Self-hosted URL |
| `ZIZKADB_TELEMETRY` | Set `false` to opt out |

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
