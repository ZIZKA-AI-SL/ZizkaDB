# Troubleshooting

## OSS quickstart (localhost)

| Problem | Fix |
|---------|-----|
| Docker not running | Start Docker Desktop or OrbStack |
| `API did not become healthy` | First pull can take 1–2 min. Check `docker compose -f ~/.zizkadb/infra/docker-compose.quickstart.yml logs api` |
| GHCR image pull fails | Clone repo: `git clone … && bash scripts/quickstart.sh` (builds locally) |
| `docker compose ... --build` hangs/fails on `load metadata for docker.io/library/...` with `DeadlineExceeded` | Registry metadata check timed out even though the base image is already cached locally. Pre-pull once: `docker pull python:3.12-slim && docker pull node:20-alpine`, then re-run `bash scripts/setup-local.sh` — the build reuses the cached layers and finishes in seconds. |
| Port 8000 / 3001 in use | Stop other services or edit compose ports |
| Dashboard empty after demo | Login → **Open my dashboard →** → agent **support-bot** |
| `/health` is 200 but events fail | `/health` is liveness only. Check `curl http://localhost:8000/health/deep` for Postgres/Redis/Qdrant |
| `zizkadb demo` fails | Ensure API healthy: `curl http://localhost:8000/health` |
| `curl: (6) Could not resolve host: raw.githubusercontent.com` | DNS/network issue on your machine. Try another DNS (`1.1.1.1`), toggle VPN, or clone the repo and run `bash scripts/setup-local.sh` |
| `ERROR: Can not perform a '--user' install. User site-packages are not visible in this virtualenv.` | Your global pip config forces `--user`. Run `pip config unset global.user` (or `export PIP_USER=0`) and re-run |
| `ModuleNotFoundError: No module named 'zizkadb'` right after installing (macOS, Python 3.13+) | Python 3.13 skips hidden `.pth` files, and macOS marks files hidden inside a `.venv` in iCloud-synced folders (Desktop/Documents). Editable installs (`pip install -e`) break. Install normally (`pip install ./sdk/python`) or move the repo out of Desktop/Documents |
| `localhost:3001` shows the marketing website / signup instead of **Open my dashboard** | Old dashboard image. Pull the latest: `docker compose -f ~/.zizkadb/infra/docker-compose.quickstart.yml pull dashboard && docker compose -f ~/.zizkadb/infra/docker-compose.quickstart.yml up -d dashboard` |
| Login page: "not running in self-hosted mode" | Set `DEPLOYMENT_MODE=self_hosted` in `infra/.env` and restart the API |
| Login page: "Dashboard login is turned off" | `ENV=production` without `SELFHOST_ADMIN_TOKEN`. Set it in `infra/.env` and restart the API |

---

## API key shows "Never used"

No successful authenticated request reached the API.

| Check | Action |
|-------|--------|
| Wrong env var name | Use `ZIZKADB_API_KEY` or `AGENTDB_API_KEY`, not `API=` |
| Space in key | Re-paste as one continuous line |
| Wrong URL | Must be `https://db.zizka.ai/v1/events` not `/v1_` |
| Placeholder in curl | Use real key, not `YOUR_KEY` |
| Revoked key | Create a new key in Settings |

Test from server:

```bash
cd ~/your-app
export $(grep -E '^ZIZKADB_API_KEY=' .env | xargs)
curl -s -w "\nHTTP %{http_code}\n" \
  -H "Authorization: Bearer ${ZIZKADB_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"agent":"YOUR_AGENT","event":"test","data":{}}' \
  https://db.zizka.ai/v1/events
```

---

## HTTP 401 Invalid API key

- Key typo, space, or truncated paste
- Key revoked
- Using JWT where API key expected (or vice versa)

---

## HTTP 403 Agent mismatch

Your key is scoped to one agent but code logged to another.

```
Key for: maria-agent
Code sent: conv-user123
```

**Fix:** Use the same agent name in code. On ZizkaDB Cloud you can instead create a **tenant-wide key** (Settings).

### `409 agent_limit_reached` or `401` "not active" on self-host

Self-hosted ZizkaDB runs **one agent with one API key**. The error message names the agent this instance uses.

- Log with that agent name; use `session_id` to separate users or conversations.
- Only the oldest active API key works. To switch keys, revoke the old one in the dashboard.
- Agents and keys beyond the first stop working after upgrading, including ones created earlier. Their data stays in the database.
- For several agents or keys, use ZizkaDB Cloud (https://db.zizka.ai).

SDK raises `AgentScopeError` (Python/TypeScript).

---

## Self-hosted key sent to the wrong host

A key created via `http://localhost:8000` (self-hosted dashboard/signup) only exists in
that instance's Postgres — it authenticates against `localhost:8000`, never against
`https://db.zizka.ai`. If your app only sets `ZIZKADB_API_KEY` and not `ZIZKADB_HOST`,
most SDK clients default to the cloud host and every request 401s.

**Fix:** set both env vars for a self-hosted target — `ZIZKADB_HOST=http://localhost:8000`
*and* `ZIZKADB_API_KEY=zizkadb_live_...`. Verify quickly:

```bash
curl -s http://localhost:8000/v1/agents -H "Authorization: Bearer <key>"
```

200 with a JSON agent list confirms the key is valid on that host, and shows which agent
name(s) it's scoped to — use that exact name for `ZIZKADB_AGENT_NAME` / `agent=` (see
"HTTP 403 Agent mismatch" above).

---

## Dashboard empty but curl works

| Cause | Fix |
|-------|-----|
| Viewing wrong agent | Open the agent name your code uses |
| Settings test event | Goes to `dashboard-connection-test`, not your agent |
| Different tenant | SDK key and dashboard login must be same account |
| Self-host dev login vs email login | Use same auth path |

Use **Test agent** on the agent page.

---

## npm run build ENOENT (EC2)

```
Could not read package.json: .../agentdb/package.json
```

**Fix:** `cd ~/agentdb/dashboard` then `npm run build`

---

## Search returns nothing

1. Configure embeddings: Settings → Embeddings
2. Log events first (need history to search)
3. Use semantic queries, not exact string match

---

## baseline shows warming_up

Need more sessions with `session_id` on events. Keep logging.

---

## zizka.ai chat not logging to ZizkaDB

1. Use **tenant-wide key** on ZizkaDB Cloud (app logs to `conv-{userId}`)
2. Deploy founder-agent ZizkaDB migration (remove Pinecone)
3. `.env`: `AGENTDB_HOST=https://db.zizka.ai` + `AGENTDB_API_KEY=...`
4. `npm run build && pm2 restart founder-agent --update-env`
5. `curl https://zizka.ai/api/health` → should show `dbZizka`, not `pinecone`

---

## Legacy keys

`agdb_live_...` keys from early signups still authenticate. No forced rotation.

---

## Get help

- [GitHub Issues](https://github.com/Zizka-ai/ZizkaDB/issues)
- [db.zizka.ai/docs](https://db.zizka.ai/docs)
- Community: db.zizka.ai/community
