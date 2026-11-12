# ZizkaDB for OpenClaw

**Know what your OpenClaw agent did, and why.** This plugin records every turn of your [OpenClaw](https://github.com/openclaw/openclaw) agent in [ZizkaDB](https://github.com/ZIZKA-AI-SL/ZizkaDB) as a tamper-evident, causal audit trail: the message that started it, each tool call and result, the model's answer and token usage, and the reply sent back, each linked to its cause.

```
user_message → tool_call (read) → tool_result → llm_end → assistant_response
```

Then ask why any step happened: `db.why(event_id)` walks back to the message that caused it, in the ZizkaDB dashboard or from code.

- **Observe-only.** The plugin never blocks, rewrites or delays a message or a tool call, and a failed write never affects the agent.
- **Tamper-evident.** Every event is hash-chained per agent; `GET /v1/events/chain` proves nothing was changed or removed.
- **Private if you want.** `captureContent: false` records which tools and models ran, token usage and errors, without prompts, replies, tool arguments or results.
- **Cloud or self-hosted.** Use [ZizkaDB Cloud](https://db.zizka.ai) or your own server (AGPL, one `docker run`).

## Install

From [ClawHub](https://clawhub.ai):

```bash
openclaw plugins install clawhub:@zizka-ai/zizkadb
```

Or from [npm](https://www.npmjs.com/package/zizkadb-openclaw). OpenClaw asks you to confirm installs from outside ClawHub, so add `--force`:

```bash
openclaw plugins install npm:zizkadb-openclaw --force
```

OpenClaw asks you to approve the plugin's permissions on install (in scripts, pass `--accept-capabilities`). Community plugins load once they are listed in `plugins.allow`, which the config below includes.

## Configure

Add the plugin's settings to `~/.openclaw/openclaw.json`, then restart the gateway:

```json5
{
  plugins: {
    allow: ["zizkadb"],
    entries: {
      zizkadb: {
        enabled: true,
        // Lets the plugin see model outputs and token usage (OpenClaw asks for this explicitly).
        hooks: { allowConversationAccess: true },
        config: {
          apiKey: "zizkadb_live_...",          // ZizkaDB Cloud: db.zizka.ai → Settings → API keys
          // host: "http://localhost:8000",    // or your self-hosted server instead
          agent: "openclaw",                   // name in ZizkaDB (default "openclaw")
          captureContent: true,                // false: no prompts, replies, tool arguments or results
        },
      },
    },
  },
}
```

`ZIZKADB_API_KEY` and `ZIZKADB_HOST` environment variables work instead of `apiKey` and `host`.

Without `allowConversationAccess`, messages, tool calls and results are still recorded, but model outputs and token usage are not; the gateway log says so at startup.

## What is recorded

| OpenClaw hook | ZizkaDB event | Data |
|---|---|---|
| `message_received` (chat channels), or the turn's prompt from `llm_input` (CLI, API, cron) | `user_message` | text, sender, media count |
| `after_tool_call` | `tool_call` → `tool_result` or `tool_error` | tool name, arguments, result or error, duration |
| `llm_output` | `llm_end` | provider, model, answer, token usage (input, output, cache read and write) |
| `message_sent` | `assistant_response` | text sent, whether delivery succeeded |
| `agent_end` (failed run) | `agent_error` | error, duration |

Each OpenClaw turn becomes one chain, and each conversation (session key) becomes a ZizkaDB session. Events carry `metadata.source = "openclaw"`, the OpenClaw agent id and the channel. Long values are cut at 8,000 characters.

Recording happens in the running gateway, which is where OpenClaw runs chat channels, cron jobs and `openclaw agent` turns. One-shot `openclaw agent --local` runs are not recorded.

## Tools for the agent (optional)

The plugin also adds two tools, off until you allow them:

- `zizkadb_why`: the causal chain behind an event.
- `zizkadb_search`: search past events.

```json5
{ tools: { alsoAllow: ["zizkadb_why", "zizkadb_search"] } }
```

**Each tool sees only the conversation it runs in.** The scope comes from OpenClaw's trusted tool context (the conversation's session key), never from the model's arguments, so one user of a shared bot cannot read another user's records: search asks the server for this conversation's events only, `zizkadb_why` answers "not found" for an event of another conversation and leaves out ancestors from other conversations, and without a conversation the tools are not offered at all. Results are labelled as untrusted historical data, so text someone typed earlier is returned as data, not as instructions. For agent-wide audits use the ZizkaDB dashboard or the SDK with your own key.

## Telemetry

Like every ZizkaDB package, the plugin sends one anonymous install ping per gateway process, the first time it connects to ZizkaDB, to `https://db.zizka.ai/v1/telemetry`. It contains a random install id (`~/.zizkadb/install_id`), the plugin version, the Node version and OS, cloud or self-hosted, and whether it runs in CI, a container or on a machine. It never contains keys, agent names, events or your server address. Set `ZIZKADB_TELEMETRY=false` to turn it off. Details: [docs/telemetry.md](https://github.com/ZIZKA-AI-SL/ZizkaDB/blob/main/docs/telemetry.md).

## Trace a turn

```ts
import { ZizkaDB } from 'zizkadb-sdk'

const db = new ZizkaDB({ apiKey: 'zizkadb_live_...' })
const [last] = await db.query({ agent: 'openclaw', limit: 1 })
;(await db.why(last.eventId)).print()
```

Or open the agent in the ZizkaDB dashboard: sessions, the causal graph, tool error rates and token costs.

## Development

Node 24.16 or later (OpenClaw's requirement).

```bash
cd integrations/openclaw
npm install
npm run typecheck && npm test && npm run build
```

## License

AGPL-3.0-or-later, like the rest of ZizkaDB.
