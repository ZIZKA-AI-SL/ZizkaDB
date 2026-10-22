# MCP and Cursor

**Package:** `zizkadb-mcp` on PyPI (**0.1.8**)  
**Run:** `uvx zizkadb-mcp`

## Install uv (for uvx)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Setup (managed cloud)

1. [Sign up](https://db.zizka.ai/signup)
2. Dashboard → **Create agent** (e.g. `cursor-agent`) → copy key
3. Add to Cursor MCP config
4. Quit Cursor (`Cmd+Q`) and reopen
5. Use the **same agent name** when logging via MCP tools

## Cursor config (cloud)

`~/.cursor/mcp.json` or `.cursor/mcp.json` in your project:

```json
{
  "mcpServers": {
    "zizkadb": {
      "command": "uvx",
      "args": ["zizkadb-mcp"],
      "env": {
        "ZIZKADB_API_KEY": "zizkadb_live_xxxx"
      }
    }
  }
}
```

## Self-hosted

After `bash scripts/setup-local.sh`:

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

Dev key `zizkadb_dev_local` is auto-injected on localhost (matches `DEV_API_KEY` in `infra/.env`).

## MCP tools

| Tool | Purpose |
|------|---------|
| `log_event` | Record decision / action |
| `search_memory` | Semantic search |
| `get_context` | Memory for system prompt |
| `why` | Causal chain |
| `query_events` | List/filter recent events |
| `time_travel` | State at timestamp |
| `get_baseline` | Behavioral baseline vs recent sessions |
| `get_token_usage` | Token/cost aggregation for an agent |
| `get_token_optimization` | Deterministic token-waste suggestions |
| `memory_diff` | What changed after a session |
| `forget` | GDPR-style delete by metadata field |

## Example prompt in Cursor

> "Log that we chose Postgres for the agent store with parent reasoning."

Agent name in tool calls must match your dashboard agent (unless using tenant-wide key).
