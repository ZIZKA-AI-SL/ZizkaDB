# zizkadb-mcp

**PyPI:** [zizkadb-mcp 0.1.8](https://pypi.org/project/zizkadb-mcp/0.1.8/)

MCP server for [ZizkaDB](https://db.zizka.ai). Gives any MCP-compatible AI agent persistent memory, semantic search, causal debugging, and time travel.

## Setup (managed cloud)

1. [Sign up](https://db.zizka.ai/signup) at db.zizka.ai  
2. **Dashboard → Create agent** (e.g. `cursor-agent`) — copy the API key shown once  
3. Paste the key into your MCP config (see below)  
4. Reload MCP in Cursor — use the **same agent name** in `log_event` calls  
5. Events appear on your [dashboard](https://db.zizka.ai/dashboard) within seconds  

> **Agent name must match.** If your key is for `cursor-agent`, log with `agent="cursor-agent"`. For many dynamic agent names, use a **tenant-wide key** (Settings).

> **Signup alone does not log events.** You must create an agent + key and add it to `ZIZKADB_API_KEY`.

## Cursor — 30 second setup

Paste into `~/.cursor/mcp.json` (or `.cursor/mcp.json` in your project), then reload MCP:

```json
{
  "mcpServers": {
    "zizkadb": {
      "command": "uvx",
      "args": ["zizkadb-mcp"],
      "env": { "ZIZKADB_API_KEY": "zizkadb_live_xxxx" }
    }
  }
}
```

Self-host: use the [Self-hosted](#self-hosted) block below (`ZIZKADB_HOST` only — dev key auto-injected on localhost).

**Prerequisite:** install [uv](https://docs.astral.sh/uv/) for `uvx`:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Install

```bash
pip install zizkadb-mcp
# or
uvx zizkadb-mcp
```

## Self-hosted

```json
{
  "mcpServers": {
    "zizkadb": {
      "command": "uvx",
      "args": ["zizkadb-mcp"],
      "env": {
        "ZIZKADB_HOST": "http://localhost:8000"
      }
    }
  }
}
```

On localhost, the dev key (`zizkadb_dev_local`) is auto-injected — no API key needed. Must match `DEV_API_KEY` in `infra/.env` if you changed it.

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ZIZKADB_HOST` | `https://db.zizka.ai` | API base URL |
| `ZIZKADB_API_KEY` | — | **Required on cloud.** Your key from db.zizka.ai Settings |
| `AGENTDB_API_KEY` | — | Legacy alias for `ZIZKADB_API_KEY` |
| `AGENTDB_HOST` | — | Legacy alias for `ZIZKADB_HOST` |
| `ZIZKADB_TELEMETRY` | on | Set `false` to opt out (anonymous install ping when MCP server starts) |

If `ZIZKADB_API_KEY` is missing on cloud, MCP tools return a clear error instead of silently failing.

## Links

- [Docs](https://db.zizka.ai/docs)
- [GitHub](https://github.com/Zizka-ai/ZizkaDB)
