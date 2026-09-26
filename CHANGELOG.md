# Changelog

All notable changes to this project are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
