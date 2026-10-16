# Changelog

All notable changes to this project are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Impact trace: what did this event cause?

- New `GET /v1/events/{event_id}/impact?depth=` walks the causal graph downstream, the mirror of `/why`: every event that descends from the given one through `parent_event_id`, with its `depth` (anchor first, then by depth). `depth` is 0–50 (default 10). At most 500 nodes are returned; `truncated: true` says the cap was hit.
- Same scoping as `/why`: an agent-scoped key anchors on and descends through its own agent's events only, and an unbound key is bound to the anchor's agent and sees only that agent's subtree.

### Event checksums cover the audit fields

- New events get a v2 checksum over tenant, agent, event type, data, `parent_event_id`, session, timestamp, sequence number and metadata — not just event type + data. Rewriting any of these in the database now shows up as a mismatch. `embedding` and `index_status` are excluded because the embedding pipeline updates them after insert.
- The checksum is computed from the values Postgres canonicalises (JSONB, UUID, `nextval`, `now()`) and written by the INSERT itself, so JSONB number normalisation, uppercase `parent_id` UUIDs and empty metadata do not cause false mismatches, and each event is still a single row write.
- New `GET /v1/events/verify?agent=&before_seq=` and `GET /v1/events/{event_id}/verify` recompute checksums and report `valid` / `valid_legacy` / `mismatch` / `missing` (status and version only; hashes are not returned). The list pages by `sequence_no`: pass `next_before_seq` as `before_seq` until it is null. The single-event route returns 404 for an API key not yet bound to an agent.
- Events written before this release keep their old checksum and verify as `valid_legacy`; nothing is rewritten. The first start records the newest such event in `checksum_v2_cutoff`, and a v1 checksum on any later event is a `mismatch`, so a row cannot be downgraded to the weaker hash.
- Scope: the checksum is unkeyed and stored in the row. It detects edits made without recomputing it, not a database writer who recomputes it, and it cannot show a deleted event.
- **Breaking:** the `checksum` returned by `POST /v1/events` (and the SDKs' `LogResult.checksum`) is now the v2 hash. Code that recomputed `sha256({"event", "data"})` client-side to compare will no longer match.

### Compose: no development defaults on servers

- **Breaking for `infra/docker-compose.yml` users whose `infra/.env` has no `ENV` line.** `ENV` is now required; `docker compose` stops with `Set ENV in infra/.env - production for a server, development for local use only`. It used to default to `development`, which turned on the dev API key and login-free dev-token on a public port. `.env.example` no longer sets `ENV` (a server copy would otherwise default to development); `scripts/setup-local.sh` adds `ENV=development` to a local `infra/.env`, new or existing, that does not set it.
- `JWT_SECRET`, `JWT_REFRESH_SECRET` and `DEV_API_KEY` no longer default to the published values. In production the API refuses to start without real secrets; in development it uses its local-only fallbacks. A local `infra/.env` without `JWT_SECRET` therefore signs and encrypts with different dev values after upgrading: sign in again, and re-enter any BYOK embedding key saved locally.
- `EMBEDDINGS_ENABLED` and `CORS_ALLOWED_ORIGINS` from `infra/.env` now reach the API container. They were documented but silently ignored, so semantic search could not be enabled on a self-hosted Docker install.
- The embed worker now receives `DEPLOYMENT_MODE`, `EMBEDDINGS_ENABLED` and `JWT_SECRET`, so it can decrypt per-tenant BYOK embedding keys.
- Postgres now has `restart: unless-stopped`, like the other services.
- The local quickstart compose (`infra/docker-compose.quickstart.yml`) publishes every port on `127.0.0.1` only, as the single-container quickstart already did.
- `infra/lib/production-guard.sh` (used by `scripts/reset-local-db.sh`) now refuses unless `infra/.env` sets `ENV=development`; an unset `ENV` counts as production, matching the API.
- `infra/backup-postgres.sh` and `infra/backup-qdrant.sh` keep working on a server whose `infra/.env` has no `ENV` line (they only read from the running containers).
- `scripts/validate-selfhost-config.sh` follows the API: an unset `ENV` is production, and an empty `DEV_API_KEY` is accepted in production (only the published dev keys are rejected).
- Unchanged for now: data ports in `infra/docker-compose.yml` (Postgres, Redis, Qdrant) are still published on all interfaces, and `EMBEDDING_ENCRYPTION_KEY` is still not passed to the containers.

### Development mode is opt-in (unset `ENV` means production)

- **Breaking for deployments that never set `ENV`.** Only `ENV=development` (any case) enables development mode: the hardcoded dev API keys, `POST /v1/auth/dev-token`, one-click dashboard sign-in and per-process rate limits. An unset, empty or misspelt `ENV` is now treated as production. Previously an unset `ENV` meant development, so a server started without it accepted `zizkadb_dev_local` and issued dashboard tokens with no login.
- The production startup checks now run for every `ENV` other than `development` (including unset, `staging` and `Production`), not only for exactly `production`.
- Production startup refuses every published JWT placeholder for both `JWT_SECRET` and `JWT_REFRESH_SECRET`: the `.env.example` values, the Compose defaults and the `dev-secret` fallbacks (#226).
- An empty `DEV_API_KEY` is now allowed in production, as `SECURITY.md` and the staging example already said; dev keys are never accepted there anyway. A known default (`zizkadb_dev_local`, `agdb_dev_local`) is still refused.
- Not affected: the compose files, the all-in-one image, `scripts/setup-local.sh` and the hybrid setup in `CONTRIBUTING.md` already set `ENV=development` explicitly. If you run the API image or `uvicorn` yourself for local development, add `ENV=development`.

### Self-hosted is one agent, one API key

- **Breaking for multi-agent self-hosts.** A self-hosted deployment (`DEPLOYMENT_MODE=self_hosted`) now runs a single agent with a single active API key. This is always enforced and cannot be changed by env vars (`API_KEY_LIMITS_ENFORCED`, `API_KEY_LIMIT_SELF_HOSTED` and `MAX_AGENTS_SELF_HOSTED` have no effect on self-hosted).
- Logging an event under a second agent name, creating a second agent in the dashboard, or binding a key to a second agent returns `409` with `code: agent_limit_reached` and a message naming the existing agent.
- Agents that already exist keep working after upgrading; only new ones are refused. The `support-bot` demo agent and the dashboard connection test do not count toward the limit.
- `POST /v1/a2a/messages` returns 403 on self-hosted. Multiple agents, agent fleets and agent-to-agent messaging are ZizkaDB Cloud features.
- Managed deployments: new per-plan agent caps (`max_agents`, tunable with `MAX_AGENTS_<PLAN>`), enforced only when `API_KEY_LIMITS_ENFORCED=true`, as before.

### Which tool, which API

- Events that name their tool (`data.tool`) or API (`data.api`, or `method` + `url` on `api_call`) are now shown and counted by name. Existing events are covered: the name is read from the payload, with no schema change.
- Dashboard: activity list, session timeline and the Why? chain show `tool_call · lookup_order`; Reports and Agent Behavior get a "Tools & APIs called" table with calls, share and error rate per tool/API.
- API (additive): `actions` on `GET /v1/agents/{id}/report` and `/behavior-change`; `actions` + `action_distribution` on each `/baseline` window; `top_actions` on `/stats`. `biggest_changes` can now include `actions.tool:<name>` / `actions.api:<name>` entries. Drift scores are computed exactly as before.
- SDKs: `log_tool` / `log_tool_result` / `log_api` (Python) and `logTool` / `logToolResult` / `logApi` (TypeScript). `log_api` strips the query string, fragment and credentials from the URL before logging.
- LangChain: `tool_end` and `tool_error` now carry the tool name. CrewAI logger: new `log_tool` / `log_tool_result`.
- MCP proxy: tool results were never logged (the match could not succeed); results and errors are now matched to their call by JSON-RPC id and logged with the tool name as `mcp_tool_result` / `mcp_tool_error`.

### One-container quickstart

- New single image `ghcr.io/zizka-ai-sl/zizkadb` (`infra/allinone/`): Postgres 16 + pgvector, Redis, the API and the dashboard in one container, with all data in one volume. `quickstart-remote.sh` now pulls and runs it instead of five containers, and falls back to the compose stack if the image is unavailable (`ZIZKADB_QUICKSTART=compose` forces the old path).
- The quickstart no longer needs Python on the host: the container logs the demo chain itself on first start. The script checks for busy ports first, publishes on `127.0.0.1` only, and opens the dashboard on the demo agent.
- Self-hosted dashboard in one-click mode (`ENV=development`) now signs in automatically, and a deep link such as `/dashboard/activity?agent=support-bot` survives the login redirect. Admin-token mode is unchanged.
- `QDRANT_URL=""` starts the API without Qdrant when embeddings are off (the self-host default). With embeddings on, an empty `QDRANT_URL` is a startup error.
- GHCR images are now built for `linux/arm64` as well as `linux/amd64`, so Apple Silicon runs them natively. The API image no longer ships compilers.

### Self-hosted dashboard is just the dashboard

- Self-hosted builds (`NEXT_PUBLIC_DEPLOYMENT_MODE=self_hosted`, the default for the GHCR image) no longer serve the marketing site, signup, pricing, OTP login or billing UI. `http://localhost:3001/` goes straight to **Open my dashboard**.
- New `GET /v1/auth/selfhost` and `POST /v1/auth/selfhost-login`: one-click sign-in when `ENV=development`; admin-token sign-in (`SELFHOST_ADMIN_TOKEN`) when `ENV=production`; login disabled in production without a token.
- `validate-selfhost-config.sh` now requires `SELFHOST_ADMIN_TOKEN` (not `EMAIL_*`) for production self-host.
- The API refuses to start in production when `SELFHOST_ADMIN_TOKEN` is set but shorter than 16 characters.
- Docs and `validate-selfhost-config.sh` now say production needs a **unique random** `DEV_API_KEY` (the API already refused to boot with an empty/default one; the old docs said to unset it).
- `quickstart-remote.sh` installs the demo SDK into a private venv (`~/.zizkadb/venv`), so it no longer fails on a broken active venv, pip `user = true` config, or externally-managed system Pythons.
- `setup-local.sh` no longer crashes with `BUILD_FLAG[@]: unbound variable` on macOS's bash 3.2 when using pre-built images.

### OSS improvement sprint (Days 1–11, Sep 2026)

Fifteen-day self-host / OSS hardening pass. User-visible highlights:

- **CI & quality:** dashboard vitest in CI; expanded core route and SDK tests; `check-doc-drift.sh` keeps API/SDK versions aligned
- **Ops:** `X-Request-ID` on every response; `/health` vs `/health/deep` operator guidance; production startup refuses default `JWT_SECRET` / `DEV_API_KEY`; Redis-backed rate limits with fail-open in-memory fallback; CORS wildcard warning in production
- **Onboarding:** [DEVELOPMENT.md](DEVELOPMENT.md), [dashboard/README.md](dashboard/README.md), `smoke-test.sh` (curl + minimal-python example), [examples/minimal-python/README.md](examples/minimal-python/README.md) dashboard expectations
- **Docs & scope:** README self-host first + [REPO_SPLIT](docs/REPO_SPLIT.md); enterprise tier removed from OSS tree; [dependency audit snapshot](docs/ai/DEPENDENCY_AUDIT.md)
- **SDK parity:** TypeScript 0.2.8 aligned with API; vitest for `contextFor()`, `memoryDiff()`, `agents()`; `token_usage` on `log()` documented as Python-only

### Removed

- Enterprise marketing (`/enterprise`), enterprise pricing tier, and enterprise-specific docs — OSS repo is self-host only; commercial VPC lives in the private cloud repo ([docs/REPO_SPLIT.md](docs/REPO_SPLIT.md))
- `enterprise` plan from `PLAN_ENTITLEMENTS`; demo-request `source` allowlist is now `landing` and `newsletter` only

### Security

- Dev API keys (`zizkadb_dev_local`) are accepted only when `ENV` is `development` (or unset). Previously any value other than exactly `production` (e.g. `staging`, `prod`) accepted them. The API logs a `DEV MODE` warning on startup whenever they are active.
- API keys not yet bound to an agent (e.g. the key issued at signup) no longer get tenant-wide access: `GET /v1/agents` returns `[]`, `GET /v1/sessions` and `GET /v1/sessions/{id}/events` require `?agent=` (which binds the key), and `DELETE /v1/memory/forget` returns 400. Dashboard sessions keep tenant-wide access.
- `GET /v1/sessions/{id}/events` no longer binds the key to the first returned event's agent; it accepts an optional `?agent=` filter.
- `GET /v1/sessions/{id}/why/{event_id}` and `GET /v1/events/{id}/why` no longer return other agents' ancestor events to an agent-scoped key.
- `GET /v1/sessions?agent=X` with a key scoped to another agent now returns 403 (previously silently returned the key's own sessions).

### Fixed

- `DELETE /v1/memory/forget` now deletes search vectors **before** Postgres rows. If Qdrant is unreachable (with embeddings enabled) it returns **503 and deletes nothing**, so a retry is safe and no orphan vectors are left behind. The response adds `vector_cleanup: bool`.
- `forget` no longer writes the forgotten value (e.g. an email) to logs; a 12-char SHA-256 fingerprint is logged instead.
- `/health/deep` returns **503** when a required subsystem is down (was always 200). Qdrant is required only when embeddings are enabled; each check reports `required`. `scripts/run-golden-path.sh` and `scripts/smoke-test.sh` no longer use `curl -f` on it, so failures show which subsystem is down.
- `GET /v1/events/at` (`db.at()`) now returns the exact state at the requested time for any history length. It used to rebuild state from the agent's **oldest** 10,000 events (`EVENTS_AT_MAX_ROWS`), so agents with longer histories got an early state back under the requested timestamp; the response's `truncated` flag was dropped by both SDKs and the dashboard. The newest write per key is now picked in Postgres, ties on `timestamp` go to `sequence_no`, `truncated` is always `false`, `max_rows` is gone and `EVENTS_AT_MAX_ROWS` no longer has any effect. A new partial index, `idx_events_state_writes`, holds only `STATE_SET`/`STATE_DELETE` events, so agents that never write state answer without scanning their history; `init_db()` builds it on the next start, which on a large `events` table holds writes while it builds (create it `CONCURRENTLY` beforehand to avoid that).
- A `STATE_DELETE` whose `key` is not a string (a list or an object) no longer makes `GET /v1/events/at` return 500 for later timestamps of that agent; it is ignored, as a numeric or null key already was.

### Changed

- Dashboard CI runs `npm test` (vitest) alongside lint and build
- Suggestions endpoint rate limit uses Redis in production (`SUGGESTIONS_RATE_LIMIT_STORAGE`) with fail-open in-memory fallback when Redis is unavailable
- Public demo-request and community rate limits fail-open to per-worker memory when Redis is unavailable
- Production startup logs a warning when `CORS_ALLOWED_ORIGINS` is unset (wildcard `*` allowed)
- TypeScript SDK 0.2.8; API `/health` and OpenAPI version aligned with Python SDK release line; `check-doc-drift.sh` enforces version sync
- API responses include `X-Request-ID`; request start/end logged for operator tracing

### Added

- `scripts/smoke-test.sh` runs `scripts/smoke-example.sh` by default (`SKIP_EXAMPLE=1` to skip)
- TypeScript SDK vitest coverage for `contextFor()`, `memoryDiff()`, and `agents()`
- Production startup guard tests (`validate_production_startup`, dev key rejected when `ENV=production`)

### Documentation

- [examples/minimal-python/README.md](examples/minimal-python/README.md): expected `why()` output and dashboard Activity view
- [sdk/typescript/README.md](sdk/typescript/README.md): `token_usage` on `log()` is Python SDK only
- Expanded [DEVELOPMENT.md](DEVELOPMENT.md) with first SDK call and `scripts/smoke-example.sh`
- Added [dashboard/README.md](dashboard/README.md); CONTRIBUTING cross-links and clarifies dashboard ports 3000 vs 3001
- README self-host first with REPO_SPLIT link; dashboard KB middleware matcher and vitest-in-CI accuracy (§4, §13, §15)
- [DEVELOPMENT.md](DEVELOPMENT.md) operator section: `/health` liveness vs `/health/deep` readiness, `X-Request-ID` tracing
- [docs/ai/DEPENDENCY_AUDIT.md](docs/ai/DEPENDENCY_AUDIT.md): Day 11 `pip-audit` / `npm audit` snapshot (critical Next.js CVEs deferred to major upgrade)
- CHANGELOG sprint summary (Days 1–11); PR template aligned with CI jobs; release skill uses `smoke-test.sh` instead of removed `test-e2e-workflow.sh`
- README OSS scope one-liner + [REPO_SPLIT](docs/REPO_SPLIT.md); robots/sitemap comments no longer reference `/admin` in OSS tree

### Integrations

- **`zizkadb-livekit` 0.2.0** (PyPI) — LiveKit Agents voice calls → ZizkaDB Sessions + Events (transcript only, no audio stored). Depends on `zizkadb-sdk>=0.2.8` + `livekit-agents>=1.3.0`. Docs: [docs/integrations/livekit.md](docs/integrations/livekit.md), example: [examples/livekit-agent/](examples/livekit-agent/).
  - **Non-blocking writes** — events go onto a bounded background queue, so no HTTP round trip sits on the audio path
  - **Live event stream** — `attach()` captures `eot_prediction` and `user_turn_exceeded`, which never reach the session report
  - **Explicit event mapping** — ~15 LiveKit pipeline events map to distinct ZizkaDB types instead of collapsing to `livekit_event`; `agent_handoff` and `agent_config_update` chat items are no longer dropped
  - **Verbosity tiers** — `transcript` / `standard` / `verbose` via `level=` or `ZIZKADB_EVENT_LEVEL`
  - **Per-call observer registry** — `register_observer` / `pop_observer` replace the module-global observer, which let concurrent calls on one worker overwrite each other's transcripts
  - **Causal chain integrity** — writes are serialized so the background writer and `ingest_session_report()` cannot fork `parent_id` into parallel strands
  - **Connection pooling** — one keep-alive connection per call instead of one per event
  - **Lifecycle** — `flush()`, idempotent `aclose()`, and async-context-manager support


## [0.2.8] — 2026-09-02

### Python SDK (`zizkadb-sdk` 0.2.8, PyPI)

- Mark **Production/Stable** on PyPI with full package metadata (keywords, audit-trail description)

### MCP (`zizkadb-mcp` 0.1.7)

- Mark **Production/Stable** on PyPI; updated description and keywords

### Integrations

- `zizkadb-langchain` **0.1.3** — Production/Stable; requires `zizkadb-sdk>=0.2.8`
- `zizkadb-crewai` **0.1.3** — Production/Stable; requires `zizkadb-sdk>=0.2.8`

### Install telemetry (OSS adoption)

- **Python SDK 0.2.8**, **MCP 0.1.7**, **langchain 0.1.3**, **crewai 0.1.3**: count **installs** at import/client/server start (including self-hosted localhost), not on first API call
- Per-package telemetry keys: `python`, `langchain`, `crewai`, `livekit`, `mcp`, `typescript`, `docker`
- **Docker quickstart**: anonymous install ping (`sdk=docker`, `mode=self-hosted`) after stack is healthy
- **`sdk_telemetry`**: composite primary key `(install_id, sdk)` so Python + MCP + Docker on one machine each count separately

## [0.2.7] — 2026-08-22

### Python SDK (`zizkadb-sdk` 0.2.7, PyPI)

- Restore telemetry ping on client construct (process start), including self-hosted localhost

### TypeScript SDK (`zizkadb-sdk` 0.2.7, npm)

- Restore telemetry ping on client construct (process start), including self-hosted localhost

### MCP (`zizkadb-mcp` 0.1.6)

- Restore telemetry ping on MCP server startup (not first tool call only)

### Integrations

- `zizkadb-langchain` **0.1.2** — requires `zizkadb-sdk>=0.2.7`
- `zizkadb-crewai` **0.1.2** — requires `zizkadb-sdk>=0.2.7`

## [0.2.6] — 2026-08-21

### Python SDK (`zizkadb-sdk` 0.2.6, PyPI)

- Telemetry pings after the first successful `log()` (usage, not import)
- Stable anonymous `install_id` per machine (`~/.zizkadb` + machine-id fallback)

### TypeScript SDK (`zizkadb-sdk` 0.2.6, npm)

- Telemetry pings after the first successful `log()` (usage, not constructor)
- Stable anonymous `install_id` per machine (`~/.zizkadb` + machine-id fallback)
- Self-hosted / local installs skip telemetry (matches Python SDK)

### MCP (`zizkadb-mcp` 0.1.5)

- Telemetry pings after the first successful tool call (not server startup)
- Stable `install_id` fallback for CI/Docker

### Integrations

- `zizkadb-langchain` **0.1.1** — requires `zizkadb-sdk>=0.2.6`
- `zizkadb-crewai` **0.1.1** — requires `zizkadb-sdk>=0.2.6`

### Documentation

- Telemetry copy updated (trust page, technical brief, READMEs)
- Examples and templates pin `zizkadb-sdk>=0.2.6`

## [0.1.0] — 2026-08-21

Production-hardening sprint merged to `main` (via stacked PRs #125–#139), including:

- CI: dashboard vitest, integration workflow
- Security: scoped-key isolation on `why()`, search, verify-otp rate limits
- Dashboard: `apiFetch` timeout, 401 handling, `lib/plans.ts`
- SDK: analytics methods (`baseline`, `token_usage`, `token_optimization`)
- MCP: analytics tools
- CLI: `why`, `baseline`, `token-usage`, `token-opt`
- Docs: always-on Cursor coding standards, KB OSS scope updates

See [GitHub releases](https://github.com/Zizka-ai/ZizkaDB/releases) for tagged artifacts.

[Unreleased]: https://github.com/Zizka-ai/ZizkaDB/compare/v0.2.8...HEAD
[0.2.8]: https://github.com/Zizka-ai/ZizkaDB/compare/v0.2.7...v0.2.8
[0.2.7]: https://github.com/Zizka-ai/ZizkaDB/compare/v0.2.6...v0.2.7
[0.2.6]: https://github.com/Zizka-ai/ZizkaDB/compare/v0.1.0...v0.2.6
[0.1.0]: https://github.com/Zizka-ai/ZizkaDB/releases
