---
redu_schema: 2
repo: github.com/ZIZKA-AI-SL/ZizkaDB
plan:
  route: app
  dockerfile: infra/allinone/Dockerfile
  context: .
  port: 3001
  vm: m1.medium
  database: none
  data_paths:
    - /data
  env:
    ENV: production
    TRUSTED_PROXIES: "127.0.0.1,::1,10.0.0.0/8"
quirks:
  - "One container runs Postgres, Redis, the API and the dashboard: start no managed datastores."
  - "Choose the full dname up front and pass ZIZKADB_PUBLIC_URL=https://<dname>."
  - "Never pass ENV=development: it enables a public demo key and one-click sign-in."
  - "/health/deep returns 200 with qdrant required:false; only a 503 is a failure."
---

# ZizkaDB on Redu Cloud

Deploy the open-source edition of [ZizkaDB](https://github.com/ZIZKA-AI-SL/ZizkaDB) on [Redu Cloud](https://redu.cloud) straight from this repository. ZizkaDB records what your AI agent does and why, so you can trace any decision back to its cause.

## 1. Deploy

Add the redu MCP to your coding agent and ask:

> Deploy https://github.com/ZIZKA-AI-SL/ZizkaDB to redu

The agent reads this file and sets up:

| | |
| --- | --- |
| Machine | 1 VM, `m1.medium` (2 vCPU, 4 GB RAM), about £0.03 per hour |
| Container | Postgres, Redis, API and dashboard in one container, built from `infra/allinone/Dockerfile` |
| Address | One HTTPS URL on port 3001: the dashboard, with the API under `/v1` |
| Data | Kept in `/data`, which survives restarts and redeploys |
| Settings | `ENV=production`, `ZIZKADB_PUBLIC_URL=https://<your URL>`, `TRUSTED_PROXIES=127.0.0.1,::1,10.0.0.0/8` |

The first build takes about 5 minutes.

## 2. First sign-in

1. **Get your admin token over SSH.** Your agent can give you the SSH command.

   ```bash
   sudo podman exec redu-app sed -n 's/^GEN_SELFHOST_ADMIN_TOKEN=//p' /data/secrets.env
   ```

2. **Open your URL and sign in with the token.** Keep the token private.
3. **Create your API key** in **Settings**. Copy it straight away, because it is shown only once.

## 3. Connect your agent

```bash
pip install zizkadb-sdk          # or: npm install zizkadb-sdk
export ZIZKADB_HOST=https://<your URL>
export ZIZKADB_API_KEY=zizkadb_live_...
```

The open-source edition runs one agent per key. The first agent name you log with becomes that agent. Your events appear in the dashboard under **Activity**.

## 4. Update

Ask your agent to deploy the repository again under the same name. It rebuilds on the same machine and keeps your URL, data and admin token.

## Troubleshooting

| You see | Fix |
| --- | --- |
| "Invalid API key" | The key must come from this deployment's **Settings**. Check that `ZIZKADB_HOST` is your Redu URL. |
| The login page asks for a token you don't have | Read it over SSH (step 2). |
| A health check fails | Open `https://<your URL>/health/deep`. Postgres and Redis must be `ok`. Qdrant is not included, so a `qdrant` error is expected. |

## Notes

- **Privacy.** The server sends nothing by default. The SDKs send one anonymous install ping, which you can turn off with `ZIZKADB_TELEMETRY=false`. See [docs/telemetry.md](docs/telemetry.md).
- **Not included.** Semantic search needs Qdrant, which is part of the full Docker Compose setup. Multi-agent features are part of [ZizkaDB Cloud](https://db.zizka.ai).
- **Licence.** The server, dashboard and SDKs are AGPL-3.0, and the MCP server is MIT. See [LICENSE](LICENSE).

---

<details>
<summary>For maintainers</summary>

- redu reads the front matter at the top of this file: `plan` holds the deploy settings, and `quirks` holds known pitfalls. Add new pitfalls there, one short line each.
- Never put secrets, tokens or deployment URLs in this file.
- redu may update the managed block and the deploy log below.

</details>

<!-- redu:managed:start -->
<!-- redu:managed:end -->

## Deploy log
