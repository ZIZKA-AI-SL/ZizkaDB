# REST API

**Base URL:** `https://db.zizka.ai`  
**Prefix:** `/v1/...`  
**Auth:** `Authorization: Bearer <api_key>` or dashboard JWT  
**Explorer:** [db.zizka.ai/swagger](https://db.zizka.ai/swagger)

## Log event

```http
POST /v1/events
Content-Type: application/json
Authorization: Bearer zizkadb_live_...

{
  "agent": "my-bot",
  "event": "user_message",
  "data": { "text": "hello" },
  "parent_id": null,
  "session_id": "sess_001"
}
```

Response **201:**

```json
{
  "event_id": "...",
  "timestamp": "...",
  "sequence_no": 112,
  "checksum": "..."
}
```

## Query events

```http
GET /v1/events?agent=my-bot&limit=50
Authorization: Bearer ...
```

## Semantic search

```http
POST /v1/search
{"query": "billing errors", "agent": "my-bot", "limit": 10}
```

## Why (causal chain)

```http
GET /v1/events/{event_id}/why?depth=10
```

## Agents

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/agents` | List agents (a key not yet bound to an agent gets `[]`) |
| POST | `/v1/agents` | Create agent + first key (dashboard JWT only) |
| DELETE | `/v1/agents/{id}` | Delete agent + events + keys |
| POST | `/v1/agents/{id}/test-event` | Dashboard test (JWT) |
| GET | `/v1/agents/{id}/api-keys` | List keys |
| POST | `/v1/agents/{id}/api-keys` | Create key (dashboard JWT only) |
| DELETE | `/v1/agents/{id}/api-keys/{key_id}` | Revoke key |

## Auth / keys

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/auth/request-otp` | Send OTP — body `{email, intent:"login"|"signup"}`. Login returns **404** if email unknown; signup returns **409** if already registered. |
| POST | `/v1/auth/verify-otp` | Get JWT — body `{email, otp, intent:"login"|"signup", gdpr_consent?, marketing_consent?}`. Login only works for existing users; signup requires `gdpr_consent:true` for new accounts. |
| POST | `/v1/auth/api-keys` | Tenant-wide key (dashboard JWT only) |
| GET | `/v1/auth/api-keys` | List all keys |
| GET | `/v1/auth/api-keys/usage` | Plan key quota `{plan, limit, used, unlimited, at_limit}` |
| DELETE | `/v1/auth/api-keys/{id}` | Revoke key |

**API key creation** requires a dashboard login session (JWT), not an API key. Active keys per tenant are limited by plan (Self-Hosted 1, Pro 2, Team 5; unknown/no plan unlimited); exceeding the limit returns `409` with `{detail:{code:"api_key_limit_reached", plan, limit, used}}`. Enforcement is gated by the `API_KEY_LIMITS_ENFORCED` server flag; self-hosted deployments resolve their plan via `DEPLOYMENT_MODE=self_hosted`, not the `users.plan` column.

## Memory

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/memory/context` | Prompt-ready context for an agent (`agent` required) |
| GET | `/v1/memory/diff` | What changed after a session |
| DELETE | `/v1/memory/forget` | GDPR-style delete by metadata field (agent-scoped key: own agent only; unbound key: 400; dashboard: tenant-wide). Deletes vectors first; 503 with nothing deleted if the search index is down — safe to retry. Response includes `vector_cleanup` |

Scoped API keys must match the `agent` on these routes (`assert_agent_allowed`).

## Analytics (per agent)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/agents/{id}/report` | Date-ranged report (`from`, `to`, `granularity`) |
| GET | `/v1/agents/{id}/suggestions` | Evidence-backed AI suggestions (`refresh=1` bypasses cache) |
| GET | `/v1/agents/{id}/baseline` | Behavioral baseline |
| GET | `/v1/agents/{id}/token-usage` | Token/cost aggregation |
| GET | `/v1/agents/{id}/token-optimization` | Deterministic token-waste detectors |

## Health

```http
GET /health
→ {"status":"ok","version":"0.1.0"}
```

`GET /health/deep` checks Postgres, Redis, and Qdrant. Use that for real dependency status; `/health` is liveness only.

## Common status codes

| Code | Meaning |
|------|---------|
| 201 | Event created |
| 401 | Invalid/revoked API key |
| 403 | Agent-scoped key used with wrong agent name |
| 404 | Agent or event not found |
| 422 | Validation error (e.g. invalid demo request source) |
| 429 | Rate limit exceeded |

## Demo requests (public, no auth)

Landing **Book demo** form.

```http
POST /v1/demo-requests
Content-Type: application/json

{
  "first_name": "Ada",
  "last_name": "Lovelace",
  "email": "ada@example.com",
  "company_name": "Example Corp",
  "website": "https://example.com",
  "position": "Head of Platform",
  "source": "landing",
  "botcheck": ""
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `first_name`, `last_name`, `email`, `company_name`, `website` | yes | Trimmed server-side |
| `position` | no | Role/title (max 120 chars) |
| `source` | no | Allowlist: `landing`, `newsletter` — invalid → **422** |
| `botcheck` | no | Honeypot; non-empty → **400** |

Response **201:** `{ "id": "<uuid>", "created_at": "<iso>" }`. Rate limit: 8 requests / hour / IP (**429**). Listing demo requests is a managed-cloud operator action — there is no `/v1/admin` router in this OSS repo.
