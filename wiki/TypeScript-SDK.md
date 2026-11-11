# TypeScript SDK

**Package:** `zizkadb-sdk` on npm (**0.7.0**)

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

## Auto-capture LLM calls

`instrument(db, { agent: 'my-bot', clients: [openai, anthropic] })` records every OpenAI (chat completions and Responses API), Anthropic, Mistral, Cohere and AWS Bedrock (Converse) call, tool call and tool result as linked events with token usage, no `log()` calls needed. `await handle.flush()` before exit; `handle.uninstrument()` to stop. Details in the [SDK README](https://github.com/ZIZKA-AI-SL/ZizkaDB/tree/main/sdk/typescript#auto-capture-llm-calls-one-line).

## Vercel AI SDK

`wrapLanguageModel({ model, middleware: zizkadbMiddleware(db, { agent: 'my-bot' }) })` records any AI SDK model (Gemini, OpenAI, Anthropic, DeepSeek, Mistral, Groq, Ollama, ...) through `generateText` / `streamText`, including tool loops and reasoning. AI SDK 4, 5 and 6.

## Turn a bad session into a test

`npx zizkadb-sdk test from-session <session_id> --agent my-bot --entry src/agent.ts:answer` writes a snapshot and a vitest test that replays the session's inputs through your agent and checks the same tool path without errors. It runs inside `capture({ clients: [OpenAI] }, fn)`, which records in memory with no server.

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
