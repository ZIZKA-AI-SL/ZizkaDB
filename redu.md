---
redu_schema: 2
repo: github.com/Zizka-ai/ZizkaDB
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
  - "Build infra/allinone/Dockerfile with context '.' (the repo root). It runs Postgres, Redis, the API and the dashboard in one container, so start no managed datastores: database 'none', one VM."
  - "Expose port 3001 only. A front proxy there serves the dashboard and routes /v1, /health and /swagger to the API; port 8000 is the API alone and does not need to be public."
  - "Keep /data (data_paths). Postgres, Redis and the generated secrets live there; losing it loses the agent history and the admin token."
  - "Pass the deployment's own URL as ZIZKADB_PUBLIC_URL (https://<dname>), using a full '<label>-<8 chars>.redu.cloud' dname chosen up front. It sets the links the API generates and the only browser origin the API accepts."
  - "Set TRUSTED_PROXIES to include the range redu's HTTPS proxy connects from (10.0.0.0/8 on the private network), so login rate limits count each visitor, not the proxy."
  - "The image runs in production mode. Never pass ENV=development on a public URL: it turns on the well-known dev API key and one-click sign-in."
  - "First sign-in uses the admin token generated on first start; read it over SSH with: sudo podman exec <container> sed -n 's/^GEN_SELFHOST_ADMIN_TOKEN=//p' /data/secrets.env. Never write it into this file."
  - "Expected health: /health/deep answers HTTP 200 with postgres and redis ok, and qdrant not initialized with required:false (the single container runs without Qdrant, so semantic search and embeddings are off). A 503 is a real failure."
---

# ZizkaDB on Redu Cloud

ZizkaDB is a causal event database for AI agents: log every agent action, trace why a decision was made, and give agents memory of past runs. This file tells redu how to deploy the open-source edition from this repository, unchanged.

## Deploy

Add the redu MCP to your coding agent and ask it to "Deploy https://github.com/Zizka-ai/ZizkaDB to redu". The agent reads this file and uses the plan above:

| Resource | Value |
| --- | --- |
| App VM | one `m1.medium` (2 vCPU / 4 GB) |
| Build | `infra/allinone/Dockerfile`, context `.` |
| Public port | 3001: dashboard, with the API under `/v1` |
| Database | none managed: Postgres and Redis run inside the container |
| Persistent data | `/data` |
| Environment | `ZIZKADB_PUBLIC_URL=https://<your deployment URL>`, `TRUSTED_PROXIES=127.0.0.1,::1,10.0.0.0/8` |

## After it is live

1. **Read the admin token over SSH.** Never paste it anywhere public.

   ```bash
   sudo podman exec <container> sed -n 's/^GEN_SELFHOST_ADMIN_TOKEN=//p' /data/secrets.env
   ```

2. **Open your deployment URL and sign in with the token.**
3. **Create the API key.** Go to Settings and create an API key. The open-source edition runs one agent with one API key.
4. **Point your agent at the deployment:**

   ```bash
   export ZIZKADB_HOST=https://<your deployment URL>
   export ZIZKADB_API_KEY=zizkadb_live_...
   ```

   Then use the SDK as usual (`pip install zizkadb-sdk`, or `npm install zizkadb-sdk`).

## Notes

- **Telemetry.** The server sends nothing by default. The SDKs send one anonymous install ping, which you can turn off with `ZIZKADB_TELEMETRY=false`. The full list is in [docs/telemetry.md](docs/telemetry.md).
- **Not included.** Semantic search and embeddings need Qdrant and are not part of the single container. Multi-agent features are part of ZizkaDB Cloud.
- **Licence.** The ZizkaDB server, dashboard and SDKs are licensed under the AGPL-3.0, and the MCP server under MIT. See [LICENSE](LICENSE).

<!-- redu:managed:start -->
<!-- redu:managed:end -->

## Quirks and fixes

The recorded fixes are the `quirks:` list in the front matter at the top of this file. That list is the part redu reads back, so add new ones there. Never put secrets in this file.

## Deploy log
