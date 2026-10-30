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
  - "Separate database VPS: run infra/redu-db/setup.sh there and pass DATABASE_URL with its private IP; see 'Two machines'."
  - "Never add EVENT_RETENTION_DAYS to an existing install without the owner's yes: it deletes older events."
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

## Two machines: a website VPS and a private database VPS

ZizkaDB also runs on two small machines, with Postgres on its own VPS that only the website VPS can reach. It needs image version 0.6.0 or later.

| | Website VPS | Database VPS |
| --- | --- | --- |
| Size | 1 vCPU, 2 GB RAM, 10 GB disk | 1 vCPU, 1 GB RAM, 2 GB disk |
| Runs | The container: Redis, API and dashboard | Postgres 16 with pgvector |
| Network | Public HTTPS URL | Private network only |

1. **Prepare the database VPS.** Copy `infra/redu-db/setup.sh` to it and run it as root. It installs Postgres, tunes it for 1 GB of RAM and a small disk, listens on the private address only, and accepts only the website VPS range.

   ```bash
   ZIZKADB_DB_PASSWORD='<long random password>' APP_CIDR=10.0.0.0/8 bash setup.sh
   ```

2. **Deploy the container on the website VPS** as in step 1, with these extra settings. Percent-encode special characters in the password.

   ```bash
   DATABASE_URL=postgresql://zizkadb:<password>@<database VPS private IP>:5432/zizkadb
   WEB_CONCURRENCY=1
   ```

   On first start the container loads the schema into the empty database. It never runs the schema again once the tables exist, even with a new `/data`.

3. **Keep the database disk bounded.** Each event takes about 4 KB with its indexes, so 1 GB holds roughly 250,000 events. On a new install, set `EVENT_RETENTION_DAYS` (for example `30`) to delete older events automatically.

Measured on these sizes: the website container uses about 180 MB of RAM and Postgres about 80 MB. The dashboard build fits in 2 GB without swap. The server sends nothing out by default, so the 5 GB monthly egress covers only your own dashboard and API traffic.

Back up two things: the database VPS, and `/data/secrets.env` on the website VPS. If `secrets.env` is lost, the admin token and sign-in sessions change, but your data stays in the database.

## Troubleshooting

| You see | Fix |
| --- | --- |
| "Invalid API key" | The key must come from this deployment's **Settings**. Check that `ZIZKADB_HOST` is your Redu URL. |
| The login page asks for a token you don't have | Read it over SSH (step 2). |
| "The external database ... is not reachable" or "Could not prepare the external database" | Check the private IP, `APP_CIDR` and password, then re-run `setup.sh` on the database VPS. |
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
