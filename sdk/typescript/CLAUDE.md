# sdk/typescript/ — TypeScript SDK

See root [`CLAUDE.md`](../../CLAUDE.md) for full project context.

Package: **`zizkadb-sdk` 0.3.0** on npm. Import: `import { ZizkaDB } from 'zizkadb-sdk'`.

---

## Client pattern

```typescript
import { ZizkaDB } from 'zizkadb-sdk'

const db = new ZizkaDB({ apiKey: 'zizkadb_live_...' })
// local: const db = new ZizkaDB({ host: 'http://localhost:8000' })

const user = await db.log({ agent: 'my-bot', event: 'user_message', data: { text: 'Hello' } })
const tool = await db.log({ agent: 'my-bot', event: 'tool_call', data: {}, parentId: user.eventId })

const chain = await db.why(tool.eventId)
```

**Sync constructor** — unlike the Python SDK, no `async with` is needed.

---

## Key differences from Python SDK

| Topic | Python SDK | TypeScript SDK |
|---|---|---|
| Constructor | `async with ZizkaDB(...) as db:` | `new ZizkaDB({ ... })` |
| Field names | `snake_case` (`parent_id`, `event_id`) | `camelCase` (`parentId`, `eventId`) |
| LangChain adapter | Yes (`ZizkaDBCallbackHandler`) | No |
| CrewAI adapter | Yes (`ZizkaDBCrewLogger`) | No |
| CLI | `zizkadb init` + analytics subcommands | No |
| Analytics | `baseline()`, `tokenUsage()`, `tokenOptimization()` | Yes (Python + TS) |

---

## Error types

- `AuthError` — invalid or revoked API key
- `AgentScopeError` — API key is scoped to a different agent (HTTP 403)

---

## Files

- `src/index.ts` — `ZizkaDB` class and all public methods
- `src/types.ts` — TypeScript type definitions

---

## Tests

```bash
cd sdk/typescript && npm test
```
