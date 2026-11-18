# ZizkaDB documentation index

Start here if you are not sure which file to read.

## I want to…

| Goal | Start here |
|------|------------|
| **Understand the product in 2 minutes** | [README](../README.md) |
| **Connect my existing agent** | [CONNECT.md](../CONNECT.md) → [integrate/](integrate/) |
| **Run locally (Docker)** | `bash scripts/setup-local.sh` · [Getting Started wiki](../wiki/Getting-Started.md) |
| **Self-host production** | [wiki/Self-Hosting](../wiki/Self-Hosting.md) · [Production deployment](../wiki/Production-Deployment.md) |
| **Use managed cloud** | [db.zizka.ai/signup](https://db.zizka.ai/signup) |
| **Browse API** | [Swagger](https://db.zizka.ai/swagger) · [REST API wiki](../wiki/REST-API.md) |
| **Product docs (hosted site)** | [db.zizka.ai/docs](https://db.zizka.ai/docs) |
| **Examples** | [examples/](../examples/) · `zizkadb init my-agent --template basic` |
| **Contribute** | [CONTRIBUTING.md](../CONTRIBUTING.md) · [CLAUDE.md](../CLAUDE.md) |
| **Troubleshoot** | [wiki/Troubleshooting](../wiki/Troubleshooting.md) |
| **Architecture / why** | [docs/adr/](adr/) · [wiki/Architecture](../wiki/Architecture.md) |

## Integration guides (in this repo)

| Guide | Status |
|-------|--------|
| [Any agent (framework-agnostic)](integrate/any-agent.md) | **Start here** for custom stacks |
| [Supported vs generic frameworks](integrate/frameworks.md) | LangChain, CrewAI, LiveKit, LangGraph, OpenClaw, etc. |
| [LiveKit (voice agents)](integrations/livekit.md) | `zizkadb-livekit` — one call → one session |
| [OpenClaw](../integrations/openclaw/) | Plugin `@zizka-ai/zizkadb` — every turn recorded with its cause |
| [LangChain](integrations/langchain.md) | Callback handler package |
| [CrewAI](integrations/crewai.md) | Crew logger package |
| [CONNECT.md](../CONNECT.md) | Copy-paste: Python, TS, LangChain, CrewAI, LiveKit, OpenClaw, MCP, REST |

## Source of truth

| Topic | Canonical location |
|-------|-------------------|
| HTTP API routes | FastAPI `/swagger` (runtime) |
| Dashboard API client | `dashboard/lib/api.ts` |
| Dashboard behavior | `dashboard/DASHBOARD_KNOWLEDGE_BASE.md` |
| Plan API key caps | `core/services/entitlements.py` |
| Auth rules | `docs/adr/004-auth-dependency-split.md` |
| AI coding in this repo | [AGENTS.md](../AGENTS.md) · [docs/ai/CODING_STANDARDS.md](ai/CODING_STANDARDS.md) · [docs/ai/](ai/) |

## Plan limits (honest)

- **Self-hosted (this repository):** one agent with one API key, always enforced; no monthly event cap.
- **ZizkaDB Cloud:** agents and API keys (Pro 3, Team 10; one key per agent) and events per calendar month (Pro 500,000, Team 1,500,000), enforced by the managed service.
