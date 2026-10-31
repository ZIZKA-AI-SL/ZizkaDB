# ZizkaDB in GitHub Actions

Two GitHub Actions run ZizkaDB inside your CI job, next to your agent's tests. Each starts the open-source single container in the runner: no account, no API key, and the recorded data never leaves the runner.

| Action | What it does | Marketplace |
|--------|--------------|-------------|
| [**ZizkaDB Why**](https://github.com/ZIZKA-AI-SL/zizkadb-why-action) | Explains why your agent failed: the causal chain from the failed step back to its root cause, in the job summary and a PR comment | [Listing](https://github.com/marketplace/actions/zizkadb-why-explain-ai-agent-failures) |
| [**ZizkaDB Audit**](https://github.com/ZIZKA-AI-SL/zizkadb-audit-action) | Proves the recorded history is intact: hash-chain check per agent, exported history, and an attested audit report | [Listing](https://github.com/marketplace/actions/zizkadb-audit-verify-ai-agent-history) |

```yaml
- uses: ZIZKA-AI-SL/zizkadb-why-action@v1
  with:
    run: pytest tests/agent
```

## How it works

1. The action starts `ghcr.io/zizka-ai-sl/zizkadb` on `127.0.0.1` in the runner, with an empty database.
2. It runs your command with `ZIZKADB_HOST` and `ZIZKADB_API_KEY` set. The Python and TypeScript SDKs read both, so `ZizkaDB()` with no arguments logs to it.
3. Your agent logs each step with `log()`. Passing `parent_id` links a step to the step that caused it; that link is what `why()` follows.
4. After the command, the action reads the history through the REST API: `GET /v1/events/{id}/why` for causal chains, `GET /v1/events/chain` for the hash-chain check, `GET /v1/events/export` for the NDJSON export.
5. The container is deleted at the end of the job.

To report on your own ZizkaDB instead (for example a self-hosted server), pass `host` and `api-key`.

## Make your agent log

```python
from zizkadb import ZizkaDB   # pip install zizkadb-sdk

async with ZizkaDB() as db:  # uses ZIZKADB_HOST and ZIZKADB_API_KEY
    ask = await db.log(agent="my-agent", event="user_message", data={"text": question})
    await db.log(agent="my-agent", event="tool_call", data={"tool": "search"}, parent_id=ask.event_id)
```

Framework callbacks (LangChain, CrewAI, LangGraph, LiveKit), TypeScript and MCP: [CONNECT.md](../../CONNECT.md).

## One agent per install

The open-source edition records **one agent per install**, and the CI instance is a fresh install. Log all steps under one agent name. When a test logs a second name, the API answers `409 agent_limit_reached` and the action's report explains it. Multiple agents, fleets and agent-to-agent recording are part of [ZizkaDB Cloud](https://db.zizka.ai).

## Open a CI run on your machine

Both actions upload the run's history (`zizkadb-history` or `zizkadb-audit` artifact) with `open_in_dashboard.py`:

```bash
docker run -d --name zizkadb-run -e ENV=development -e ZIZKADB_SEED_DEMO=false \
  -p 127.0.0.1:8000:8000 -p 127.0.0.1:3001:3001 ghcr.io/zizka-ai-sl/zizkadb
python3 open_in_dashboard.py *.ndjson
```

Then click any event and open **Why? (causal)**.

## Next

- [Quickstart](../../README.md#quickstart-60-seconds): ZizkaDB on your machine in about a minute
- [Self-hosting](../../wiki/Self-Hosting.md): run it on your own server
- [How the hash chain works](../security/assessment.md)
