# ZizkaDB basic agent

```bash
cp .env.example .env
pip install -r requirements.txt
python agent.py
```

Logs a user message and tool call, then prints the causal chain with `why()`.

**Local stack:** `curl -fsSL https://raw.githubusercontent.com/ZIZKA-AI-SL/ZizkaDB/main/scripts/quickstart-remote.sh | bash`  
**Dashboard:** http://localhost:3001/dashboard/activity?agent=my-agent

On localhost, each `db.log()` prints `→ zizkadb why <event_id>`. Disable with `export ZIZKADB_QUIET=1`.
