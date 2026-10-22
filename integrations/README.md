# ZizkaDB framework integrations

Official adapters for agent frameworks. Each package is optional — core SDK is always `zizkadb-sdk`.

| Package | Registry | Install | Telemetry `sdk` | Use case |
|---------|----------|---------|-------------------|----------|
| `zizkadb-sdk` | PyPI **0.3.0** | `pip install zizkadb-sdk` | `python` | Core Python client |
| [langchain](langchain/) | PyPI **0.1.3** | `pip install zizkadb-langchain` | `langchain` | LangChain callback handler |
| [crewai](crewai/) | PyPI **0.1.3** | `pip install zizkadb-crewai` | `crewai` | CrewAI crew logger |
| [livekit](livekit/) | PyPI **0.2.0** | `pip install zizkadb-livekit` | `livekit` | LiveKit voice → Sessions + Events |
| [mcp](../mcp/) | PyPI **0.1.8** | `uvx zizkadb-mcp` | `mcp` | Cursor / Claude Desktop tools |
| TypeScript SDK | npm **0.3.0** | `npm install zizkadb-sdk` | `typescript` | Node / Bun / Deno client |
| Docker OSS | — | `bash scripts/quickstart.sh` | `docker` | Self-hosted stack |

Install pings are anonymous, opt-out (`ZIZKADB_TELEMETRY=false`), and fire at **import / client init / MCP start / Docker health** — not on API calls. PyPI and npm **download** stats are separate third-party metrics.

**Configure once (all packages):**

```bash
export ZIZKADB_API_KEY=zizkadb_live_...
export ZIZKADB_AGENT=my-bot          # must match dashboard agent name
# Self-host: export ZIZKADB_HOST=http://localhost:8000
# Optional: export ZIZKADB_TELEMETRY=false
```

Monorepo dev:

```bash
pip install -e sdk/python -e integrations/langchain -e integrations/crewai -e integrations/livekit
```

Scaffold a project:

```bash
pip install zizkadb-sdk
zizkadb init my-agent --template langchain
```
