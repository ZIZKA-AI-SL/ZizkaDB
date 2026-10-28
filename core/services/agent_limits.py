"""Per-plan cap on how many agents a tenant may have.

Self-hosted (open source) runs **one** agent; multiple agents are a ZizkaDB
Cloud feature. The cap is checked wherever an agent can come into existence:
the dashboard create route, the first event logged under a new agent name, and
an API key being bound to an agent on first use.

Agents that already exist are never blocked, so an install that had several
agents before the cap keeps working — it just cannot add more.
"""

from __future__ import annotations

import logging

from services.billing import fetch_effective_plan
from services.entitlements import agent_limit_for_plan, limits_enforced
from services.exceptions import conflict

log = logging.getLogger(__name__)

# Built-in agents that never use up the slot: the quickstart / `zizkadb demo`
# agent and the dashboard's "test connection" ping.
UNCOUNTED_AGENTS = frozenset({"support-bot", "dashboard-connection-test"})

_CLOUD_URL = "https://db.zizka.ai"


async def lock_tenant(conn, tenant_id: str) -> None:
    """Take the tenant's transaction-level advisory lock.

    Account delete always takes it, before any key or agent row (see
    services/account.py). Agent and API key creation take it only while plan
    limits are enforced (always on self-hosted), and then before any row lock.
    """
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", tenant_id)


async def count_agents(conn, tenant_id: str) -> int:
    """Agents that count toward the plan cap."""
    return int(
        await conn.fetchval(
            "SELECT COUNT(*) FROM agents WHERE tenant_id = $1 AND agent_id <> ALL($2::text[])",
            tenant_id,
            list(UNCOUNTED_AGENTS),
        )
    )


async def assert_agent_slot(conn, *, tenant_id: str, agent_id: str) -> None:
    """Raise 409 if creating ``agent_id`` would exceed the plan's agent cap.

    MUST be called inside a transaction on ``conn``, immediately before the
    agent is inserted: a per-tenant advisory lock serializes concurrent
    creates so two new agents cannot both take the last slot.

    Fail-open on a plan lookup error, like the API-key cap.
    """
    if not limits_enforced() or agent_id in UNCOUNTED_AGENTS:
        return

    await lock_tenant(conn, tenant_id)

    exists = await conn.fetchval(
        "SELECT 1 FROM agents WHERE tenant_id = $1 AND agent_id = $2",
        tenant_id,
        agent_id,
    )
    if exists:
        return

    try:
        plan = await fetch_effective_plan(conn, tenant_id)
    except Exception as e:  # fail-open
        log.warning(
            "agent limit: plan lookup failed for tenant %s, allowing create: %s",
            tenant_id,
            e,
        )
        return

    limit = agent_limit_for_plan(plan)
    if limit is None:
        return

    used = await count_agents(conn, tenant_id)
    if used < limit:
        return

    if plan == "self_hosted":
        current = await conn.fetchval(
            """
            SELECT agent_id FROM agents
            WHERE tenant_id = $1 AND agent_id <> ALL($2::text[])
            ORDER BY first_seen
            LIMIT 1
            """,
            tenant_id,
            list(UNCOUNTED_AGENTS),
        )
        msg = (
            f"Self-hosted ZizkaDB runs a single agent, and this instance already has "
            f"'{current}'. Keep logging as agent='{current}', or use ZizkaDB Cloud "
            f"({_CLOUD_URL}) to run multiple agents."
        )
    else:
        msg = (
            "You've reached the maximum number of agents allowed for your "
            f"current plan ({used}/{limit}). Upgrade your subscription to "
            "create additional agents, or remove an existing one to free up a slot."
        )

    raise conflict(
        detail={
            "msg": msg,
            "code": "agent_limit_reached",
            "plan": plan,
            "limit": limit,
            "used": used,
        },
    )


async def reserve_agent_slot(pool, *, tenant_id: str, agent_id: str) -> None:
    """Make sure ``agent_id`` exists, or raise 409 if the plan has no room.

    For paths that create agents implicitly (event writes, key binding). The
    common case — the agent already exists — is one indexed lookup with no
    lock; only a brand-new agent name takes the lock and the cap check.
    """
    if not limits_enforced() or agent_id in UNCOUNTED_AGENTS:
        return

    exists = await pool.fetchval(
        "SELECT 1 FROM agents WHERE tenant_id = $1 AND agent_id = $2",
        tenant_id,
        agent_id,
    )
    if exists:
        return

    async with pool.acquire() as conn:
        async with conn.transaction():
            await assert_agent_slot(conn, tenant_id=tenant_id, agent_id=agent_id)
            # Take the slot inside the lock so a concurrent first event for a
            # different new agent sees it.
            await conn.execute(
                """
                INSERT INTO agents (agent_id, tenant_id)
                VALUES ($1, $2)
                ON CONFLICT (agent_id, tenant_id) DO NOTHING
                """,
                agent_id,
                tenant_id,
            )
