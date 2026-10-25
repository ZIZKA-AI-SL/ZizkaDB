"""Event retention: delete events older than an agent's retention period.

The period is ``agents.retention_days`` when set, else ``EVENT_RETENTION_DAYS``;
neither set means events are kept forever (the default). A background task in
the API (``retention_loop``) purges every ``RETENTION_INTERVAL_SECONDS``; an
advisory lock makes only one worker run each pass.

Each deleted event leaves an audit-chain tombstone (reason ``retention``) and
its vector is removed, exactly like a GDPR forget.
"""

from __future__ import annotations

import asyncio
import logging
import os

from services.audit_chain import tombstone_events
from services.entitlements import embeddings_enabled

log = logging.getLogger(__name__)

RETENTION_LOCK_ID = 7_301_884_222
PURGE_BATCH = 1000


def default_retention_days() -> int | None:
    raw = (os.getenv("EVENT_RETENTION_DAYS") or "").strip()
    if not raw:
        return None
    try:
        days = int(raw)
    except ValueError:
        log.warning("EVENT_RETENTION_DAYS is not a whole number; retention is off")
        return None
    return days if days > 0 else None


async def purge_expired(pool, *, batch: int = PURGE_BATCH) -> int:
    """Delete expired events for every agent; returns how many were deleted."""
    from api.memory import _delete_vectors

    default_days = default_retention_days()
    agents = await pool.fetch(
        """
        SELECT tenant_id, agent_id, COALESCE(retention_days, $1::int) AS days
        FROM agents
        WHERE COALESCE(retention_days, $1::int) IS NOT NULL
        """,
        default_days,
    )
    total = 0
    for agent in agents:
        while True:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    ids = [r["event_id"] for r in await conn.fetch(
                        """
                        SELECT event_id FROM events
                        WHERE tenant_id = $1 AND agent_id = $2
                          AND timestamp < NOW() - make_interval(days => $3)
                        ORDER BY sequence_no
                        LIMIT $4
                        FOR UPDATE SKIP LOCKED
                        """,
                        agent["tenant_id"], agent["agent_id"], agent["days"], batch,
                    )]
                    if not ids:
                        break
                    # Same lock as forget(), so the embed worker cannot
                    # re-create a vector for a row being deleted.
                    for eid in sorted(ids, key=str):
                        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", str(eid))
                    if embeddings_enabled() and not await _delete_vectors(ids, str(agent["tenant_id"])):
                        log.warning("Retention skipped a batch: search index unavailable")
                        return total
                    await tombstone_events(conn, str(agent["tenant_id"]), ids, "retention")
                    await conn.execute(
                        "DELETE FROM events WHERE tenant_id = $1 AND event_id = ANY($2::uuid[])",
                        agent["tenant_id"], ids,
                    )
                    await conn.execute(
                        """
                        UPDATE agents SET event_count = GREATEST(event_count - $3, 0)
                        WHERE tenant_id = $1 AND agent_id = $2
                        """,
                        agent["tenant_id"], agent["agent_id"], len(ids),
                    )
            total += len(ids)
            if len(ids) < batch:
                break
    return total


async def run_once(pool) -> int | None:
    """One purge pass, unless another worker holds the retention lock."""
    async with pool.acquire() as lock_conn:
        if not await lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", RETENTION_LOCK_ID):
            return None
        try:
            deleted = await purge_expired(pool)
        finally:
            await lock_conn.execute("SELECT pg_advisory_unlock($1)", RETENTION_LOCK_ID)
    if deleted:
        log.info("Retention deleted %s expired events", deleted)
    return deleted


async def retention_loop(get_pool) -> None:
    interval = max(int(os.getenv("RETENTION_INTERVAL_SECONDS", "3600")), 60)
    while True:
        try:
            await run_once(get_pool())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Retention pass failed; retrying next interval")
        await asyncio.sleep(interval)
