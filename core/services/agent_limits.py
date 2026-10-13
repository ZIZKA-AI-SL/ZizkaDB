"""Per-plan cap on how many agents a tenant may have.

Self-hosted (open source) runs **one** agent; multiple agents are a ZizkaDB
Cloud feature. The cap is strict: the agents a plan pays for are the oldest
ones, and every other agent is refused — for reads as well as writes, and
whether it is new or already existed before the cap was introduced.

Checked on every per-agent route (``api.deps.assert_agent_allowed``) and
wherever an agent can come into existence: the dashboard create route, the
first event logged under a new agent name, and an API key being bound on
first use.
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


async def _plan_and_limit(executor, tenant_id: str) -> tuple[str | None, int] | None:
    """``(plan, agent cap)``, or ``None`` when no cap applies.

    Fail-open on a plan lookup error, like the API-key cap.
    """
    if not limits_enforced():
        return None
    try:
        plan = await fetch_effective_plan(executor, tenant_id)
    except Exception as e:  # fail-open
        log.warning("agent limit: plan lookup failed for tenant %s, allowing: %s", tenant_id, e)
        return None
    limit = agent_limit_for_plan(plan)
    if limit is None:
        return None
    return plan, limit


async def _agents_in_plan(executor, tenant_id: str, limit: int) -> list[str]:
    """The agents the plan covers: the oldest ``limit`` that count toward it."""
    rows = await executor.fetch(
        """
        SELECT agent_id FROM agents
        WHERE tenant_id = $1 AND agent_id <> ALL($2::text[])
        ORDER BY first_seen, agent_id
        LIMIT $3
        """,
        tenant_id,
        list(UNCOUNTED_AGENTS),
        limit,
    )
    return [r["agent_id"] for r in rows]


def _limit_error(plan: str | None, limit: int, in_plan: list[str]):
    if plan == "self_hosted":
        current = in_plan[0] if in_plan else "your agent"
        msg = (
            f"Self-hosted ZizkaDB runs a single agent, and this instance's agent is "
            f"'{current}'. Use agent='{current}', or use ZizkaDB Cloud "
            f"({_CLOUD_URL}) to run multiple agents."
        )
    else:
        msg = (
            "You've reached the maximum number of agents allowed for your "
            f"current plan ({len(in_plan)}/{limit}). Upgrade your subscription to "
            "use additional agents, or remove an existing one to free up a slot."
        )
    return conflict(
        detail={
            "msg": msg,
            "code": "agent_limit_reached",
            "plan": plan,
            "limit": limit,
            "used": len(in_plan),
        },
    )


async def agents_visible_in_plan(executor, tenant_id: str) -> list[str] | None:
    """Agent ids a tenant may see under its plan, or ``None`` for no restriction."""
    capped = await _plan_and_limit(executor, tenant_id)
    if capped is None:
        return None
    _, limit = capped
    return [*await _agents_in_plan(executor, tenant_id, limit), *sorted(UNCOUNTED_AGENTS)]


async def assert_agent_within_plan(executor, *, tenant_id: str, agent_id: str) -> None:
    """Raise 409 unless ``agent_id`` is covered by the plan or a slot is free.

    Read-only: does not create or reserve anything. ``executor`` is a pool or
    a connection.
    """
    if agent_id in UNCOUNTED_AGENTS:
        return
    capped = await _plan_and_limit(executor, tenant_id)
    if capped is None:
        return
    plan, limit = capped

    in_plan = await _agents_in_plan(executor, tenant_id, limit)
    if agent_id in in_plan or len(in_plan) < limit:
        return
    raise _limit_error(plan, limit, in_plan)


async def assert_agent_slot(conn, *, tenant_id: str, agent_id: str) -> None:
    """Like ``assert_agent_within_plan`` but safe to follow with an INSERT.

    MUST be called inside a transaction on ``conn``, immediately before the
    agent is inserted: a per-tenant advisory lock serializes concurrent
    creates so two new agents cannot both take the last slot.
    """
    if not limits_enforced() or agent_id in UNCOUNTED_AGENTS:
        return
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", tenant_id)
    await assert_agent_within_plan(conn, tenant_id=tenant_id, agent_id=agent_id)


async def reserve_agent_slot(pool, *, tenant_id: str, agent_id: str) -> None:
    """Make sure ``agent_id`` may be written to, creating it if a slot is free.

    For paths that create agents implicitly (event writes, key binding). An
    agent already covered by the plan costs one indexed lookup and no lock;
    only a brand-new agent name takes the lock.
    """
    if agent_id in UNCOUNTED_AGENTS:
        return
    capped = await _plan_and_limit(pool, tenant_id)
    if capped is None:
        return
    plan, limit = capped

    in_plan = await _agents_in_plan(pool, tenant_id, limit)
    if agent_id in in_plan:
        return
    if len(in_plan) >= limit:
        raise _limit_error(plan, limit, in_plan)

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


async def count_agents(conn, tenant_id: str) -> int:
    """Agents that count toward the plan cap."""
    return int(
        await conn.fetchval(
            "SELECT COUNT(*) FROM agents WHERE tenant_id = $1 AND agent_id <> ALL($2::text[])",
            tenant_id,
            list(UNCOUNTED_AGENTS),
        )
    )
