# ZizkaDB basic agent

Minimal **log → `parent_id` → `why()`** example. Two events (`user_message` → `tool_call`) and a printed causal chain.

## Prerequisites

1. ZizkaDB API running (local: `bash scripts/setup-local.sh` from repo root).
2. Python 3.10+.

## Run locally

From this directory:

```bash
cp .env.example .env   # optional; localhost uses dev key auto-injection
pip install -r requirements.txt
export ZIZKADB_HOST=http://localhost:8000   # your server URL if self-hosted; omit for ZizkaDB Cloud
python agent.py
```

From repo root (health check + SDK install + agent):

```bash
bash scripts/smoke-example.sh
# or full stack smoke (curl + example):
bash scripts/smoke-test.sh
```

Set `SKIP_EXAMPLE=1` on `smoke-test.sh` to run API curl checks only.

## Expected terminal output

`why()` prints a tree with two nodes — root `user_message`, child `tool_call`:

```
Causal chain for evt_... (length=2)
├── user_message
│   data: {"text": "Hello from my first agent"}
└── tool_call
    data: {"tool": "echo", "args": {"message": "hello"}}
```

(Event IDs and formatting may vary; both event types must appear.)

## Expected dashboard

1. Open http://localhost:3001/dashboard/activity?agent=my-agent (dev login: **Open my dashboard**).
2. Agent filter: **my-agent** (override with `ZIZKADB_AGENT` in `.env`).
3. Activity list shows **user_message** and **tool_call** with timestamps; open an event to inspect `parent_id` linkage.

If the list is empty, confirm the API URL (`ZIZKADB_HOST`) matches your stack and `ENV=development` for localhost dev keys.

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `ZIZKADB_HOST` | `http://localhost:8000` | Self-hosted API base URL |
| `ZIZKADB_API_KEY` | (dev key on localhost) | Managed cloud or production |
| `ZIZKADB_AGENT` | `my-agent` | Agent name in events and dashboard filter |

See [`.env.example`](.env.example).
