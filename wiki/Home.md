# ZizkaDB Wiki

**Don't observe — audit your AI agent.** ZizkaDB is a causal database for AI agents: causal lineage, time-travel debugging, session replay, a tamper-evident audit trail, agent memory, semantic search and dashboards. It is an agentic database built for agent auditability, and its record supports EU AI Act Article 12 logging as part of EU AI Act compliance.

| Resource | Link |
|----------|------|
| GitHub | [ZIZKA-AI-SL/ZizkaDB](https://github.com/ZIZKA-AI-SL/ZizkaDB) |
| OSS quickstart (no clone) | `curl -fsSL https://raw.githubusercontent.com/ZIZKA-AI-SL/ZizkaDB/main/scripts/quickstart-remote.sh \| bash` |
| Connect guide | [CONNECT.md](https://github.com/ZIZKA-AI-SL/ZizkaDB/blob/main/CONNECT.md) |
| PyPI SDK | [zizkadb-sdk](https://pypi.org/project/zizkadb-sdk/) **0.7.0** |
| PyPI MCP | [zizkadb-mcp](https://pypi.org/project/zizkadb-mcp/) **0.1.14** |
| PyPI LangChain | [zizkadb-langchain](https://pypi.org/project/zizkadb-langchain/) **0.1.5** |
| PyPI CrewAI | [zizkadb-crewai](https://pypi.org/project/zizkadb-crewai/) **0.1.5** |
| PyPI LiveKit | [zizkadb-livekit](https://pypi.org/project/zizkadb-livekit/) **0.2.3** |
| npm SDK | [zizkadb-sdk](https://www.npmjs.com/package/zizkadb-sdk) **0.7.0** |
| OpenClaw plugin | [ClawHub @zizka-ai/zizkadb](https://clawhub.ai/plugins/@zizka-ai/zizkadb) · [npm zizkadb-openclaw](https://www.npmjs.com/package/zizkadb-openclaw) **0.1.0** |
| Docs site | [db.zizka.ai/docs](https://db.zizka.ai/docs) |
| Managed cloud (optional) | [db.zizka.ai/signup](https://db.zizka.ai/signup) |

---

## What is ZizkaDB?

When an agent misbehaves in production, scattered logs are not enough. ZizkaDB stores **every agent decision as an event** with optional `parent_id` links:

| Need | Primitive |
|------|-----------|
| Why did the agent do that? | `why(event_id)` |
| What did it know at 2pm Tuesday? | `at(agent, timestamp)` |
| Find similar past failures | `search()` / `context_for()` |
| Is this agent drifting? | Agent drift measurement: `baseline(agent)` drift score + dashboard |

**Not** a vector DB alone. **Not** traces alone. An **operational database for AI agents**: every event is SHA-256 checksummed and hash-chained, so the record is cryptographic telemetry for AI agents that an agent audit can verify, and agent drift shows up as a measured score.

---

## Quick start (OSS — recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/ZIZKA-AI-SL/ZizkaDB/main/scripts/quickstart-remote.sh | bash
```

No signup. No API key on localhost. Runs `zizkadb demo` and opens the dashboard at http://localhost:3001/login.

**Have the repo?**

```bash
git clone https://github.com/ZIZKA-AI-SL/ZizkaDB.git && cd ZizkaDB
bash scripts/quickstart.sh
```

See [[Getting Started]] · [worked example 01](https://github.com/ZIZKA-AI-SL/ZizkaDB/tree/main/worked/01-support-order-delay)

---

## Quick start (managed cloud)

1. [Sign up](https://db.zizka.ai/signup) at db.zizka.ai
2. **Dashboard → Create agent** (e.g. `my-bot`) — copy the API key shown once
3. `pip install zizkadb-sdk`
4. Log with the **same agent name** in every `db.log()` call
5. Open [dashboard](https://db.zizka.ai/dashboard)

Details: [[Agents and API Keys]]

---

## Wiki pages

- [[Getting Started]] — OSS quickstart, first event, connect your code
- [[Agents and API Keys]] — per-agent vs tenant-wide keys
- [[Python SDK]] · [[TypeScript SDK]] · [[MCP and Cursor]] · [[REST API]]
- [[Multi-Agent Apps]]
- [[Self-Hosting]] · [[Production Deployment]]
- [[Troubleshooting]]
- [[Architecture]]

---

## Packages

| Package | Install | Use |
|---------|---------|-----|
| Python SDK | `pip install zizkadb-sdk` | `from zizkadb import ZizkaDB` |
| LiveKit (voice) | `pip install zizkadb-livekit` | `ZizkaDBLiveKitObserver` — one call → one session |
| OpenClaw plugin | `openclaw plugins install clawhub:@zizka-ai/zizkadb` | Every OpenClaw turn recorded with its cause ([guide](https://github.com/ZIZKA-AI-SL/ZizkaDB/blob/main/integrations/openclaw/README.md)) |
| MCP server | `uvx zizkadb-mcp` | Cursor, Claude Desktop |
| TypeScript SDK | `npm install zizkadb-sdk` | Node / Next.js apps |

Legacy env alias: `AGENTDB_API_KEY` (same as `ZIZKADB_API_KEY`).

---

## License

- API, dashboard, SDKs: **AGPL-3.0**
- MCP server: **MIT**
