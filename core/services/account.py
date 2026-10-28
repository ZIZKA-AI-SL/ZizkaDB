"""Managed-cloud account lifecycle — delete account or one-time trial extension."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from db.connection import get_pool, get_qdrant, QDRANT_COLLECTION
from services.agent_limits import lock_tenant
from services.billing import fetch_user_billing

log = logging.getLogger(__name__)

RETENTION_TRIAL_DAYS = int(os.getenv("RETENTION_TRIAL_DAYS", "30"))


def managed_cloud_only() -> bool:
    return os.getenv("ENV", "development") == "production"


async def account_options(*, user_id: str) -> dict:
    if not managed_cloud_only():
        return {"managed_cloud": False}

    row = await fetch_user_billing(user_id=user_id)
    if not row:
        return {"managed_cloud": False}

    return {
        "managed_cloud": True,
        "retention_trial_available": not bool(row.get("retention_trial_used")),
        "retention_trial_days": RETENTION_TRIAL_DAYS,
        "trial_ends_at": row["trial_ends_at"].isoformat() if row.get("trial_ends_at") else None,
        "plan": row.get("plan"),
        "email": row.get("email"),
    }


async def grant_retention_trial(*, user_id: str) -> dict:
    if not managed_cloud_only():
        raise ValueError("Account options are only available on managed cloud")

    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT user_id, email, retention_trial_used, trial_ends_at
        FROM users WHERE user_id = $1::uuid
        """,
        user_id,
    )
    if not row:
        raise ValueError("Account not found")
    if row["retention_trial_used"]:
        raise ValueError("You have already used your extra free month")

    new_trial_end = datetime.now(timezone.utc) + timedelta(days=RETENTION_TRIAL_DAYS)

    await pool.execute(
        """
        UPDATE users
        SET trial_ends_at = $2,
            subscription_status = 'trialing',
            retention_trial_used = TRUE
        WHERE user_id = $1::uuid
        """,
        user_id,
        new_trial_end,
    )

    return {
        "message": f"Your trial has been extended by {RETENTION_TRIAL_DAYS} days.",
        "trial_ends_at": new_trial_end.isoformat(),
        "retention_trial_available": False,
    }


async def _purge_tenant_vectors(tenant_id: str) -> None:
    try:
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

        qdrant = get_qdrant()
        await qdrant.delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))]
                )
            ),
        )
    except Exception as e:
        log.warning("Qdrant purge for tenant %s failed: %s", tenant_id, e)


async def delete_managed_account(*, user_id: str, tenant_id: str) -> None:
    if not managed_cloud_only():
        raise ValueError("Account deletion is only available on managed cloud")

    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT user_id, email, tenant_id
        FROM users WHERE user_id = $1::uuid
        """,
        user_id,
    )
    if not row:
        raise ValueError("Account not found")
    if str(row["tenant_id"]) != str(tenant_id):
        raise PermissionError("Tenant mismatch")

    email = row["email"]
    tid = str(row["tenant_id"])

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Lock order shared with writes, erasures, agent delete and key
            # creation: the tenant lock, then its key and agent rows in a fixed
            # order, and only then the tenant row (via the deletes). Taking
            # the tenant row first deadlocks with a write that holds an agents
            # row and still has to insert its event.
            await lock_tenant(conn, tid)
            # Keys before agents: binding a key on first use updates the key
            # row, then its foreign key needs the agent row.
            await conn.execute(
                """
                SELECT 1 FROM api_keys WHERE tenant_id = $1::uuid
                ORDER BY key_id
                FOR UPDATE
                """,
                tid,
            )
            await conn.execute(
                """
                SELECT 1 FROM agents WHERE tenant_id = $1::uuid
                ORDER BY agent_id COLLATE "C"
                FOR UPDATE
                """,
                tid,
            )
            await conn.execute("DELETE FROM users WHERE user_id = $1::uuid", user_id)
            await conn.execute("DELETE FROM auth_otps WHERE email = $1", email)
            await conn.execute("DELETE FROM tenants WHERE tenant_id = $1::uuid", tid)

    # After the commit: a write that finished while the delete waited may have
    # added a vector, and once the tenant is gone no write can add another.
    # A failed delete leaves its data's vectors in place.
    await _purge_tenant_vectors(tid)

    log.info("Managed account deleted user_id=%s tenant_id=%s", user_id, tid)
