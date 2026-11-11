# zizkadb-sdk

**npm:** [zizkadb-sdk 0.7.0](https://www.npmjs.com/package/zizkadb-sdk)

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

## Auto-capture LLM calls (one line)

```ts
import OpenAI from 'openai'
import Anthropic from '@anthropic-ai/sdk'
import { ZizkaDB, instrument } from 'zizkadb-sdk'

const db = new ZizkaDB({ apiKey: 'zizkadb_live_xxxx' })
const openai = new OpenAI()
const anthropic = new Anthropic()

const zdb = instrument(db, { agent: 'my-bot', clients: [openai, anthropic] })
```

Then call the clients as usual. Every call is recorded as causally linked events, so `db.why()` works without any `db.log()`:

`user_message` → `llm_end` (model, output, `token_usage`) → `tool_call` → `tool_result` → `llm_end` …

- Covers OpenAI `chat.completions.create` and `responses.create`, which also means every OpenAI-compatible API: **DeepSeek, GLM (Zhipu), Kimi (Moonshot)**, Qwen, Mistral, Groq, Together, OpenRouter, and local servers (**Ollama**, vLLM, LM Studio). Anthropic `messages.create` covers Claude, including on Bedrock and Vertex. Reasoning text (`reasoning_content`) and each provider's cached-token fields are recorded.
- Native SDKs in `clients` too: **Mistral** (`@mistralai/mistralai`: `chat.complete`, `chat.stream`), **Cohere** (`cohere-ai` `CohereClientV2`: `chat`, `chatStream`) and **AWS Bedrock** (`@aws-sdk/client-bedrock-runtime`: `send()` of `ConverseCommand` / `ConverseStreamCommand`; other commands pass through untouched).
- Calls return the client's own promise, so `.withResponse()` and other extras keep working. Recording runs in the background and never changes the result or the error.
- `await zdb.flush()` waits for pending writes, e.g. before a script or serverless function exits.
- `captureContent: false` stores only models, tool names, token usage and the links.
- **Streaming** (`stream: true`) is recorded too: iterate the client's stream as usual (its extras, such as `controller`, are untouched); once it ends the events carry the full output, tool calls and token usage (for OpenAI chat, usage needs `stream_options: { include_usage: true }`). A stream you stop early (`break`) is marked `truncated: true`; one that fails mid-way becomes `llm_error` with the partial output. A stream you never read does not hold up other recordings. The `.stream()` helpers (`chat.completions.stream()`, `responses.stream()`, Anthropic `messages.stream()`) are recorded too.
- `clients` also takes the client classes (`[OpenAI, Anthropic]`) to record every instance, including ones created later.
- `zdb.uninstrument()` restores the original methods.

## Vercel AI SDK (any provider)

```ts
import { generateText, wrapLanguageModel } from 'ai'
import { google } from '@ai-sdk/google'
import { ZizkaDB, zizkadbMiddleware } from 'zizkadb-sdk'

const db = new ZizkaDB({ apiKey: 'zizkadb_live_xxxx' })
const model = wrapLanguageModel({ model: google('gemini-2.5-flash'), middleware: zizkadbMiddleware(db, { agent: 'my-bot' }) })

await generateText({ model, prompt: 'Where is order 42?', tools })
```

Wrap any AI SDK model (Google Gemini, OpenAI, Anthropic, DeepSeek, Mistral, Groq, xAI, Ollama, ...) and `generateText` / `streamText` are recorded as the same linked events, including multi-step tool loops, reasoning and token usage. Works with AI SDK 4, 5 and 6. Inside `capture()` (below) the events go to the test run instead of ZizkaDB.

## Turn a bad session into a test

```bash
npx zizkadb-sdk test from-session <session_id> --agent my-bot --entry src/agent.ts:answer
npx vitest run tests/session_<id>.test.ts
```

Writes a snapshot of the session (`tests/zizkadb_sessions/<name>.json`: user inputs, tool path, final answer, errors) and a vitest test that replays the user inputs through your agent and checks it calls the same tools, in the same order, without errors. `--clients openai,anthropic` names the LLM clients your agent uses (default `openai`). Without `--entry`, connect `runAgent()` in the generated file and set `AGENT_CONNECTED = true`. If the recorded run was the bug, edit `EXPECTED_TOOL_CALLS` to the path the agent should take. Reads `ZIZKADB_API_KEY` / `ZIZKADB_HOST` like the SDK.

The test runs inside `capture()`, which records the agent's OpenAI / Anthropic calls in memory (no ZizkaDB server; any `instrument()` in your app stays quiet meanwhile). Use it in your own tests too:

```ts
import OpenAI from 'openai'
import { capture } from 'zizkadb-sdk'

it('answers refund questions with the policy search', async () => {
  const run = await capture({ clients: [OpenAI] }, () => myAgent('How long do refunds take?'))
  expect(run.toolCalls).toEqual(['search_policies'])
  expect(run.errors).toEqual([])
})
```

## Python vs TypeScript

| Feature | Python SDK | TypeScript SDK |
|---------|------------|----------------|
| `token_usage` on `log()` | `token_usage={...}` kwarg merges into event `data` | **Not supported** — pass token counts inside `data` manually, or use the Python SDK |
| `db.token_usage()` analytics | Yes | Yes (`tokenUsage()`) |
| Auto-capture LLM calls | `zizkadb.instrument(db, agent=...)` | `instrument(db, { agent, clients })` (records `token_usage` too) |
| Other providers | Gemini, native ollama, Zhipu, Mistral, Cohere, Bedrock SDKs, LiteLLM, llama-cpp, transformers | Mistral, Cohere, Bedrock SDKs; Vercel AI SDK middleware (`zizkadbMiddleware`) for everything else |
| Session → regression test | `zizkadb test from-session` (pytest) | `npx zizkadb-sdk test from-session` (vitest) |

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

Needs a server from release 0.6.0. Full guide: [CONNECT.md](https://github.com/ZIZKA-AI-SL/ZizkaDB/blob/main/CONNECT.md#beyond-log-and-why).

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

Core HTTP client only — LangChain, CrewAI, and LiveKit adapters are Python packages (`zizkadb-langchain`, `zizkadb-crewai`, `zizkadb-livekit`). The [OpenClaw plugin](https://github.com/ZIZKA-AI-SL/ZizkaDB/blob/main/integrations/openclaw/README.md) (`zizkadb-openclaw`) is TypeScript and built on this SDK.

## Links

- [Docs](https://db.zizka.ai/docs)
- [API explorer](https://db.zizka.ai/swagger)
- [GitHub](https://github.com/ZIZKA-AI-SL/ZizkaDB)
