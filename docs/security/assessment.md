# Security assessment and threat model

This document covers ZizkaDB's actors, assets, attack surface, the most likely and most damaging threats, how the code mitigates them, and the risks that remain. It covers the open-source self-hosted product in this repository.

**Review cadence:** update it in the same PR as any change to authentication, tenant isolation, the write path, the audit chain or what is exposed on the network, and review it in full at least once per minor release.

## Actors

| Actor | Trust | Reaches ZizkaDB through |
|-------|-------|-------------------------|
| Anonymous internet client | None | The public API port and dashboard, if the operator exposes them |
| Agent holding an API key | Its own tenant, and only its agent when the key is scoped; no writes with a read-only key | REST `/v1/*`, SDKs, MCP server |
| Dashboard user | Its own account and tenant | Dashboard, JWT session after email OTP |
| Operator (self-hoster) | Full: owns the host, database and secrets | Shell, Docker, `.env` |
| Maintainers and CI | Can change code and publish releases | GitHub, Actions, PyPI, npm, GHCR |
| Third-party services | Receive data only when configured | Embedding providers (OpenAI or a local endpoint), SMTP, anonymous telemetry endpoint |

## Assets

1. **Agent event history:** prompts, tool calls and decisions, which may contain personal or business data.
2. **Integrity of that history:** the per-agent hash chain and tombstones that prove nothing was changed or silently deleted.
3. **Credentials:** API keys, JWT signing secrets, the self-host admin token, the database password, stored embedding-provider keys.
4. **Accounts:** user emails and OTP codes.
5. **Release artifacts:** images and packages that users install.

## Attack surface

| Entry point | Code |
|-------------|------|
| REST API `/v1/*` (FastAPI) | `core/api/`, auth dependencies in `core/api/deps.py` |
| Login: email OTP and JWT | `core/api/auth.py`, `core/services/auth.py` |
| Dashboard (Next.js) and the single-image front proxy | `dashboard/`, `infra/allinone/front-proxy.js` |
| MCP server (stdio, calls the REST API with a key) | `mcp/` |
| Event write path: redaction, size limits, checksum and chain | `core/services/event_write.py`, `redaction.py`, `audit_chain.py` |
| Outbound calls: embeddings, email, telemetry | `core/services/embeddings.py`, `email.py`, `docs/telemetry.md` |
| Data stores: Postgres, Redis, Qdrant | `core/db/connection.py`, `infra/docker-compose.yml` |
| Build and release pipeline | `.github/workflows/`, `scripts/publish-packages.sh` |

## Threats, mitigations and residual risk

Ordered by likelihood × impact.

| # | Threat | Mitigations in place | Residual risk |
|---|--------|---------------------|---------------|
| 1 | **Cross-tenant or cross-agent data access:** a key holder reads or changes another tenant's or agent's events | Tenant comes only from the authenticated key or JWT, never from the request body; every query is scoped by `tenant_id`; scoped keys are checked with `assert_agent_allowed()` on every per-agent route; real-Postgres tests cover tenant-scoped SQL | A new route that forgets the check. Mitigated by the review checklist in CONTRIBUTING and CLAUDE.md invariants, not by a framework-level guard |
| 2 | **Insecure deployment:** dev mode, default passwords or data ports exposed on a public host | Production is the default (`ENV` unset or misspelt = production); dev keys and one-click login exist only with `ENV=development`; a well-known `DEV_API_KEY` is dropped in production; data ports bind to `127.0.0.1`; warnings for default DB passwords and wildcard CORS | An operator can still override these. If `CORS_ALLOWED_ORIGINS` is unset in production, CORS allows all origins (logged as a warning); the API authenticates with bearer tokens, not cookies, which limits the impact |
| 3 | **Credential theft:** API keys, JWT secret or OTP brute force | API keys stored only as SHA-256 hashes of high-entropy random keys; OTPs hashed with bcrypt, expire and are rate-limited per email and IP; read-only and expiring keys available; secrets generated per install | A stolen API key works until revoked (no IP binding); key hygiene is the user's job |
| 4 | **Secrets stored in events:** agents log passwords or tokens | Redaction in `write_event()` before storage and checksum: secret-named keys are always replaced; optional patterns with `REDACT_PATTERNS`; the pre-redaction payload is never stored or logged | Secrets under innocuous key names are stored unless a pattern matches |
| 5 | **Tampering with or silently deleting history** | Per-event checksum and per-agent hash chain written under a row lock; deletes only through `delete_and_tombstone()` with tombstones and erasure receipts; `GET /v1/events/chain` and `verify_chain()` report breaks | Links are unkeyed SHA-256: an attacker with direct database write access can rebuild a consistent chain (ADR-009 H4 plans keyed sealing). Detects tampering through the API, not by a database superuser |
| 6 | **Injection:** SQL, or code execution through payloads | asyncpg with bound parameters throughout, no string-built SQL with user input; JSON payloads are stored, never executed; event size limits (`MAX_EVENT_BYTES`, 256 KB default) | Low |
| 7 | **Denial of service** through large writes or expensive queries | Request and event size limits, batch caps, Redis-backed rate limits on auth and costly routes, cursor paging, embeddings in a background worker | A valid key holder can still generate heavy load; no per-tenant quota in self-hosted beyond the one-agent cap |
| 8 | **SSRF / outbound abuse** | Outbound targets come from operator configuration (embedding URL, SMTP), not from API callers; the front proxy forwards only to fixed internal addresses; only loopback is a trusted proxy by default | An operator-configured embedding endpoint receives event text by design |
| 9 | **Supply chain:** a compromised dependency, action or release | Actions pinned to commit SHAs; lockfiles with `npm ci`; Dependabot and the Dependency review check (blocks vulnerable and malicious packages); CodeQL on every PR; least-privilege workflow permissions; images signed with cosign and shipped with an SBOM and provenance; required review on `main` | PyPI and npm packages are still uploaded by a maintainer rather than from CI with provenance (planned: [release-verification.md](release-verification.md#planned)) |
| 10 | **Data sent to third parties** | Embeddings are off unless configured; telemetry is anonymous, documented in `docs/telemetry.md` and can be turned off with `ZIZKADB_TELEMETRY=false` | Users must read the telemetry notice to know about it |

## Critical paths

These paths need two-person review and tests for every change:

- `core/api/deps.py` and every route's auth dependency (threats 1, 3)
- `core/services/event_write.py`, `redaction.py`, `audit_chain.py`, `retention.py` and anything that deletes events (threats 4, 5)
- `core/services/auth.py` and `core/api/auth.py` (threat 3)
- `infra/allinone/entrypoint.sh`, `front-proxy.js`, `core/services/runtime_env.py` (threats 2, 8)
- `.github/workflows/` and `scripts/publish-packages.sh` (threat 9)
