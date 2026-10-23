# Claude + ZizkaDB

Same as `openai-agent/`, using the Anthropic API. Logs the user message and
Claude's reply as a causal chain (`parent_id`).

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
# Self-hosted server: set both. Local dev stack: set neither. Cloud: key only.
export ZIZKADB_HOST=https://your-zizkadb.example.com
export ZIZKADB_API_KEY=zizkadb_live_...
python agent.py
```

Self-hosted ZizkaDB runs one agent per API key: the first agent name a key
logs with becomes its agent. Set `ZIZKADB_AGENT` to reuse that name.

`ANTHROPIC_MODEL` overrides the model (default `claude-opus-5-5`).
