# Development guide

Single entry for **self-hosting locally** and **contributing** to the OSS repo.

Managed-cloud marketing, `/enterprise`, and the operator admin console live in the private **zizkadb-cloud** repo — see [docs/REPO_SPLIT.md](docs/REPO_SPLIT.md).

---

## Self-host path (run ZizkaDB locally)

### Quickstart (no clone)

```bash
curl -fsSL https://raw.githubusercontent.com/ZIZKA-AI-SL/ZizkaDB/main/scripts/quickstart-remote.sh | bash
```

### Full dev stack (from a clone)

```bash
git clone https://github.com/ZIZKA-AI-SL/ZizkaDB.git && cd ZizkaDB
bash scripts/setup-local.sh
```

| Service | URL |
|---------|-----|
| API | http://localhost:8000 |
| Swagger | http://localhost:8000/swagger |
| Dashboard | http://localhost:3001 |
| Postgres | localhost:5432 |

Dev login: http://localhost:3001/login → **Open my dashboard** (no email when `ENV=development`).

Dev API key: `zizkadb_dev_local` (accepted when `ENV=development`).

**Troubleshooting:** [wiki/Troubleshooting.md](wiki/Troubleshooting.md)

Verify the stack:

```bash
bash scripts/smoke-test.sh      # health, curl checks, then minimal-python example
bash scripts/smoke-example.sh   # example only (log → why())
```

Use `SKIP_EXAMPLE=1` on `smoke-test.sh` for curl-only checks (e.g. staging without Python).

### First SDK call (Python)

With the stack running (`ENV=development`, API on :8000):

```python
import asyncio
from zizkadb import ZizkaDB

async def main():
    async with ZizkaDB(host="http://localhost:8000") as db:
        msg = await db.log(agent="my-agent", event="user_message", data={"text": "hello"})
        tool = await db.log(
            agent="my-agent",
            event="tool_call",
            data={"tool": "search"},
            parent_id=msg.event_id,
        )
        (await db.why(tool.event_id)).print()

asyncio.run(main())
```

The dev key `zizkadb_dev_local` is auto-injected for `localhost`. See [`examples/minimal-python/`](examples/minimal-python/) or run `bash scripts/smoke-example.sh`.

Check the dashboard: http://localhost:3001/dashboard/activity?agent=my-agent

### Dashboard UI only (port 3000)

```bash
cd dashboard
npm install
export NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev   # http://localhost:3000
```

Docker Compose serves the dashboard on **3001**; `npm run dev` uses **3000**.

---

## Operators (self-host / production)

### Health probes

| Endpoint | Use | Behavior |
|----------|-----|----------|
| `GET /health` | **Liveness** — is the API process responding? | Always `200` with `{"status":"ok","version":"..."}` when uvicorn is up. Does not check Postgres/Redis/Qdrant. |
| `GET /health/deep` | **Readiness** — can the stack serve real traffic? | `status: ok` when Postgres, Redis, and Qdrant all pass; `degraded` otherwise (still `200` — inspect `checks`). |

Use `/health` for load-balancer or container **liveness** probes. Use `/health/deep` after deploy, for monitoring, or before sending user traffic — `scripts/smoke-test.sh` hits both by default (`SKIP_DEEP_HEALTH=1` to skip deep).

### Request tracing

Every response includes an **`X-Request-ID`** header. Pass the same value on retries to correlate API logs (`request start` / `request end` lines in the API container). If the client omits the header, the server generates a UUID.

---

## Contributor path (change code and open a PR)

1. Read [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md).
2. **Open a GitHub issue** with the right label (`bug`, `enhancement`, `documentation`, …) — see CONTRIBUTING §Pull request workflow.
3. Branch from `main`: `git checkout -b fix/179-short-description` (include issue number when possible).
4. Run the gates for the area you touched — use [.cursor/skills/zizkadb-test/SKILL.md](.cursor/skills/zizkadb-test/SKILL.md) for the full matrix.
5. Open a PR — **first line of the description must be `Fixes #<issue>`** (issue title on the next line is helpful). CI must pass before merge.

**Dashboard work:** [dashboard/README.md](dashboard/README.md) · [dashboard/CLAUDE.md](dashboard/CLAUDE.md) · [dashboard/DASHBOARD_KNOWLEDGE_BASE.md](dashboard/DASHBOARD_KNOWLEDGE_BASE.md)

**Other module guides:** [core/CLAUDE.md](core/CLAUDE.md)

**Troubleshooting:** [wiki/Troubleshooting.md](wiki/Troubleshooting.md)

---

## Baseline

Recorded on **2026-09-04** (local macOS). Re-run before release or large refactors.

| Gate | Command | Result |
|------|---------|--------|
| Python lint | `ruff check core/ sdk/python/ mcp/ integrations/` | Pass |
| Doc drift | `bash scripts/check-doc-drift.sh` | Pass (13 routers) |
| Core unit tests | `pytest core/tests/ -m "not integration" -v` | Run locally (requires venv + deps) |
| Python SDK | `pytest sdk/python/tests/ -v` | Run locally |
| MCP | `pytest mcp/tests/ -v` | Run locally |
| TypeScript SDK | `cd sdk/typescript && npm test` | Run locally |
| Dashboard lint | `cd dashboard && npm run lint` | Pass |
| Dashboard tests | `cd dashboard && npm test` | Pass (170 tests, 27 files) |
| Dashboard build | `cd dashboard && npm run build` | Pass (29 routes) |

Full matrix (copy-paste):

```bash
ruff check core/ sdk/python/ mcp/ integrations/
bash scripts/check-doc-drift.sh
pytest core/tests/ -m "not integration" -v
pytest sdk/python/tests/ -v
pytest mcp/tests/ -v
cd sdk/typescript && npm ci && npm test
cd dashboard && npm ci && npm run lint && npm test && npm run build
```

CI runs the Python jobs, TypeScript SDK tests, and dashboard lint + **vitest** + build on every PR to `main`.
