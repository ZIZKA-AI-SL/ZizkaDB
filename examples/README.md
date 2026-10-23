# ZizkaDB examples

Runnable reference implementations. Module guide: **[CLAUDE.md](CLAUDE.md)**. Fastest path: **`zizkadb init`** (ships with the SDK).

```bash
pip install zizkadb-sdk
zizkadb init my-agent --template basic      # log + why()
zizkadb init my-agent --template openai     # AsyncOpenAI + parent_id
zizkadb init my-agent --template langchain  # callback handler
zizkadb init my-agent --template crewai     # crew logger
zizkadb init my-agent --template mcp-cursor # ~/.cursor/mcp.json
```

Voice agents: **`pip install zizkadb-livekit`** — see [livekit-agent/](livekit-agent/).

## In this folder

| Example | Description |
|---------|-------------|
| [minimal-python](minimal-python/) | `log` → `parent_id` → `why()` |
| [openai-agent](openai-agent/) | AsyncOpenAI causal logging |
| [anthropic-agent](anthropic-agent/) | Claude (Anthropic API) causal logging |
| [langchain-agent](langchain-agent/) | LangChain callbacks |
| [crewai-agent](crewai-agent/) | CrewAI kickoff + output |
| [livekit-agent](livekit-agent/) | LiveKit voice call → Sessions + Events; includes local browser UI (`web_server.py`) |
| [mcp-cursor](mcp-cursor/) | MCP config for Cursor |

## Local API

```bash
bash scripts/setup-local.sh
bash scripts/smoke-test.sh              # health + curl + minimal-python example
# or:
bash scripts/smoke-example.sh           # example only
export ZIZKADB_HOST=http://localhost:8000
python examples/minimal-python/agent.py
```

Dashboard: http://localhost:3001/dashboard/activity?agent=my-agent — see [minimal-python/README.md](minimal-python/README.md).
