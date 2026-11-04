# Agents and API Keys

## Mental model

```
Tenant (your account)
  └── Agent "support-bot"
  │     └── API key(s) scoped to support-bot only
  └── Agent "cursor-agent"
  │     └── API key(s) scoped to cursor-agent only
  └── Tenant-wide API key (optional)
        └── Can log to ANY agent name
```

---

## Per-agent keys (default)

When you **Create agent** on the dashboard:

1. Agent row is created (e.g. `maria-agent`)
2. First API key is created and bound to that agent
3. Key only works for requests where `agent` = `maria-agent`

**Use case:** One bot, one product, one integration (Cursor MCP, single app).

### Create more keys for one agent

**Settings** (header gear) with that agent selected → **New key**.

OSS self-host typically allows one active key (plan cap 1 when `API_KEY_LIMITS_ENFORCED` is on). Managed Pro/Team can add more.

### Revoke a key

Settings → trash icon. Takes effect immediately.

### Delete an agent

Deletes the agent, its events, and its keys. Cannot be undone.

---

## Tenant-wide keys (multi-agent apps)

**Managed cloud only:** **Settings → Tenant-wide API key**. OSS uses per-agent keys.

- No agent scope (`agent_id` is null)
- One key can log to `conv-user1`, `conv-user2`, `sales-agent-xyz`, etc.
- **Use when:** your app generates dynamic agent names per user/session

**Use case:** zizka.ai productivity chat (`conv-{userId}` per user).

---

## Key format

| Prefix | Status |
|--------|--------|
| `zizkadb_live_...` | Current managed-cloud keys |
| `agdb_live_...` | Legacy keys from AgentDB era — still work |

Keys are SHA-256 hashed server-side. Full key shown **once** at creation.

---

## Environment variables

| Variable | Purpose |
|----------|---------|
| `ZIZKADB_API_KEY` | Preferred |
| `AGENTDB_API_KEY` | Legacy alias (same value) |
| `ZIZKADB_HOST` | Self-hosted API URL. Used when the client gets no `host` argument, so with an API key it sends requests here instead of ZizkaDB Cloud (legacy alias `AGENTDB_HOST`) |
| `AGENTDB_HOST` | Legacy alias for host |

### Common mistakes

| Mistake | Result |
|---------|--------|
| `API=...` instead of `ZIZKADB_API_KEY=` | Key ignored (`AGENTDB_API_KEY` still works as a legacy alias) |
| Space or line break in key | 401 Invalid API key |
| Key for `agent-A`, code logs `agent-B` | 403 Agent mismatch |
| Only `pm2 restart` after `.env` change | Stale baked env — run `npm run build` for Next.js apps |

---

## Dashboard "Never used"

Means **no successful authenticated request** hit the API with that key.

- 401 = key never reached server correctly
- 403 = key reached server but wrong agent name (may still update last_used)
- Managed cloud: Settings **Send test event** uses JWT, not your API key, and logs to `dashboard-connection-test`

Use **Test agent** on Settings (with that agent selected in the header) to verify the key for that agent.
