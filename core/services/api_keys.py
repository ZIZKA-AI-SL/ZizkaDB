"""API key create/list/revoke — scoped to tenant and optionally to one agent."""

from __future__ import annotations

import logging
from uuid import UUID

from services.exceptions import conflict

from services.auth import generate_api_key
from services.billing import fetch_effective_plan
from services.entitlements import api_key_limit_for_plan, limits_enforced

log = logging.getLogger(__name__)


async def count_active_api_keys(conn, tenant_id: str) -> int:
    """Count non-revoked keys for a tenant (tenant-wide + agent-scoped).

    The ONE counter used by both the create guard and the usage endpoint so the
    enforced limit and the displayed usage can never drift apart.
    """
    return int(
        await conn.fetchval(
            "SELECT COUNT(*) FROM api_keys WHERE tenant_id = $1 AND revoked = FALSE",
            tenant_id,
        )
    )


async def assert_and_reserve_api_key_slot(conn, *, tenant_id: str) -> None:
    """Raise 409 if creating another active key would exceed the plan limit.

    MUST be called inside a transaction on ``conn`` and immediately before the
    insert. A per-tenant advisory lock serializes concurrent creates so the
    count-then-insert cannot race (rapid double-clicks, multiple tabs).

    Fail-open: if the plan lookup errors, creation is allowed — this is a
    plan-limit UX guard, not a security boundary, so a billing hiccup must never
    block a legitimate user.
    """
    if not limits_enforced():
        return

    # Serialize creates for this tenant within the surrounding transaction.
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", tenant_id)

    try:
        # Read plan on the guard's own connection so it stays inside the
        # advisory lock and never contends for a second pooled connection.
        plan = await fetch_effective_plan(conn, tenant_id)
    except Exception as e:  # fail-open
        log.warning(
            "api-key limit: plan lookup failed for tenant %s, allowing create: %s",
            tenant_id,
            e,
        )
        return

    limit = api_key_limit_for_plan(plan)
    if limit is None:
        return

    used = await count_active_api_keys(conn, tenant_id)
    if used >= limit:
        raise conflict(
            detail={
                "msg": (
                    "You've reached the maximum number of API keys allowed for your "
                    f"current plan ({used}/{limit}). Please upgrade your subscription "
                    "to create additional API keys, or delete an existing one to free up a slot."
                ),
                "code": "api_key_limit_reached",
                "plan": plan,
                "limit": limit,
                "used": used,
            },
        )


def _key_row(row) -> dict:
    return {
        "key_id": str(row["key_id"]),
        "prefix": row["key_prefix"],
        "name": row["name"],
        "agent_id": row["agent_id"],
        "created_at": row["created_at"].isoformat(),
        "last_used": row["last_used"].isoformat() if row["last_used"] else None,
    }


async def create_api_key_record(
    db,
    *,
    tenant_id: str,
    name: str | None,
    agent_id: str | None = None,
) -> dict:
    """``db`` is a pool or a transaction connection (used by the create guard)."""
    raw_key, key_hash, prefix = generate_api_key()
    row = await db.fetchrow(
        """
        INSERT INTO api_keys (tenant_id, key_hash, key_prefix, name, agent_id)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING key_id
        """,
        tenant_id,
        key_hash,
        prefix,
        name,
        agent_id,
    )
    return {
        "key_id": str(row["key_id"]),
        "key": raw_key,
        "prefix": prefix,
        "name": name,
        "agent_id": agent_id,
        "warning": "Save this key — it will not be shown again.",
    }


async def list_api_keys_for_agent(pool, tenant_id: str, agent_id: str) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT key_id, key_prefix, name, agent_id, created_at, last_used
        FROM api_keys
        WHERE tenant_id = $1 AND agent_id = $2 AND revoked = FALSE
        ORDER BY created_at DESC
        """,
        tenant_id,
        agent_id,
    )
    return [_key_row(r) for r in rows]


async def revoke_api_key_record(pool, tenant_id: str, key_id: str, agent_id: str | None = None) -> bool:
    # A malformed key_id can never match a row — and passing it to the
    # $1::uuid cast below would raise in Postgres and surface as a 500.
    # Validate it here and query with the normalised value: UUID() also
    # accepts forms Postgres rejects (e.g. a "urn:uuid:" prefix), so the
    # raw string must never reach the cast.
    try:
        key_uuid = str(UUID(key_id))
    except (ValueError, AttributeError, TypeError):
        return False
    if agent_id:
        result = await pool.execute(
            """
            UPDATE api_keys SET revoked = TRUE
            WHERE key_id = $1::uuid AND tenant_id = $2::uuid AND agent_id = $3 AND revoked = FALSE
            """,
            key_uuid,
            tenant_id,
            agent_id,
        )
    else:
        result = await pool.execute(
            """
            UPDATE api_keys SET revoked = TRUE
            WHERE key_id = $1::uuid AND tenant_id = $2::uuid AND revoked = FALSE
            """,
            key_uuid,
            tenant_id,
        )
    return result != "UPDATE 0"


async def claim_unassigned_api_key(
    *,
    key_id: str,
    tenant_id: str,
    agent_id: str,
    pool=None,
) -> str | None:
    """Atomically bind an unassigned API key to ``agent_id``.

    Returns the agent the key is bound to after this call (the requested
    agent if this request won, or the winner of a concurrent claim).
    Returns ``None`` if the key is missing, revoked, or belongs to another
    tenant — callers must not treat that as a successful bind.

    The UPDATE is conditional (``agent_id IS NULL``) so two concurrent
    requests cannot both bind the same key. The ``agents`` row is inserted
    first so the ``api_keys.agent_id`` foreign key succeeds.
    """
    if pool is None:
        from db.connection import get_pool

        pool = get_pool()
    # Binding a key to a new agent name creates that agent: the plan must
    # have room for it (409 otherwise; the key stays unassigned).
    from services.agent_limits import reserve_agent_slot

    await reserve_agent_slot(pool, tenant_id=tenant_id, agent_id=agent_id)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO agents (agent_id, tenant_id)
            VALUES ($1, $2)
            ON CONFLICT (agent_id, tenant_id) DO NOTHING
            """,
            agent_id,
            tenant_id,
        )
        result = await conn.execute(
            """
            UPDATE api_keys
            SET agent_id = $1
            WHERE key_id = $2::uuid
              AND tenant_id = $3::uuid
              AND agent_id IS NULL
              AND revoked = FALSE
            """,
            agent_id,
            key_id,
            tenant_id,
        )
        if result == "UPDATE 1":
            return agent_id
        row = await conn.fetchrow(
            """
            SELECT agent_id FROM api_keys
            WHERE key_id = $1::uuid AND tenant_id = $2::uuid AND revoked = FALSE
            """,
            key_id,
            tenant_id,
        )
    if not row or row["agent_id"] is None:
        return None
    return row["agent_id"]
