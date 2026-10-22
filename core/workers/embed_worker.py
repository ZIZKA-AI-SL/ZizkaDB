#!/usr/bin/env python3
"""Background worker — embed pending events and upsert to Qdrant.

Poison-pill fix: failed events get exponential backoff via
``next_retry_at`` and are moved to ``dead_letter`` after
``MAX_RETRIES`` attempts.  The fetch query filters out events
whose ``next_retry_at`` is in the future, so a failing event
never starves fresh pending ones.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("embed_worker")

POLL_INTERVAL = float(os.getenv("EMBED_WORKER_POLL_SEC", "2"))

MAX_RETRIES = int(os.getenv("EMBED_WORKER_MAX_RETRIES", "5"))
BASE_BACKOFF_SEC = float(os.getenv("EMBED_WORKER_BACKOFF_SEC", "5"))


def _next_retry_at(retry_count: int) -> datetime:
    """Compute the next retry timestamp using exponential backoff.

    Formula: now + BASE_BACKOFF_SEC * (2 ** retry_count)

    retry 1 → +10 s, retry 2 → +20 s, retry 3 → +40 s,
    retry 4 → +80 s, retry 5 → exceeded MAX_RETRIES → dead_letter.
    """
    delay = BASE_BACKOFF_SEC * (2 ** retry_count)
    return datetime.now(timezone.utc) + timedelta(seconds=delay)


async def _index_event(pool, row, embedding) -> bool:
    """Mark ``row`` indexed and upsert its vector, unless it was forgotten.

    The critical section runs under the per-event advisory lock (the
    codebase's pattern, cf. services/api_keys: ``pg_advisory_xact_lock``
    on ``hashtext(...)`` inside a transaction), which the GDPR forget
    path takes for the same event ids before deleting vectors and rows.
    The existence re-check inside the lock closes the race from #234:
    if forget() committed while the embedding was being generated, the
    row is gone and its vector must not be recreated. Returns False in
    that case (the caller skips the event instead of indexing it).
    """
    from db.connection import get_qdrant
    from services.event_write import _pgvector_literal
    from qdrant_client.models import PointStruct

    event_id = str(row["event_id"])
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))", event_id
            )
            still_exists = await conn.fetchval(
                "SELECT 1 FROM events WHERE event_id = $1", row["event_id"]
            )
            if still_exists is None:
                return False
            await conn.execute(
                "UPDATE events SET embedding = $1::vector, index_status = 'indexed' WHERE event_id = $2",
                _pgvector_literal(embedding),
                row["event_id"],
            )
            qdrant = get_qdrant()
            await qdrant.upsert(
                collection_name="agent_events",
                points=[
                    PointStruct(
                        id=event_id,
                        vector=embedding,
                        payload={
                            "tenant_id": str(row["tenant_id"]),
                            "agent_id": row["agent_id"],
                            "event_type": row["event_type"],
                        },
                    )
                ],
            )
    return True


async def _mark_failed(pool, row, error: str) -> None:
    """Increment ``retry_count`` and apply exponential backoff, or
    move the event to ``dead_letter`` if retries are exhausted.

    This is the core of the poison-pill fix: a permanently failing event
    will be retried ``MAX_RETRIES`` times with increasing delays, then
    parked in ``dead_letter`` so it never blocks fresh events again.
    """
    event_id = row["event_id"]
    current_retries = row.get("retry_count", 0)
    new_retry_count = current_retries + 1

    if new_retry_count >= MAX_RETRIES:
        # Exhausted — move to dead-letter queue.
        logger.error(
            "event %s moved to dead_letter after %d retries: %s",
            str(event_id), new_retry_count, error,
        )
        await pool.execute(
            """
            UPDATE events
            SET index_status = 'dead_letter',
                retry_count  = $1,
                last_error   = $2
            WHERE event_id = $3
            """,
            new_retry_count,
            error[:2000],  # cap error length
            event_id,
        )
    else:
        # Schedule a retry with exponential backoff.
        next_at = _next_retry_at(new_retry_count)
        logger.warning(
            "event %s failed (attempt %d/%d), next retry at %s: %s",
            str(event_id), new_retry_count, MAX_RETRIES,
            next_at.isoformat(), error,
        )
        await pool.execute(
            """
            UPDATE events
            SET index_status  = 'failed',
                retry_count   = $1,
                next_retry_at = $2,
                last_error    = $3
            WHERE event_id = $4
            """,
            new_retry_count,
            next_at,
            error[:2000],
            event_id,
        )


async def process_pending_batch(limit: int = 50) -> int:
    """Fetch and process up to ``limit`` embeddable events.

    The query now filters out events whose ``next_retry_at`` is in the
    future, so a recently-failed event stays hidden until its backoff
    window expires.  New ``pending`` events (``next_retry_at IS NULL``)
    are always eligible.
    """
    from db.connection import init_db, close_db, get_pool
    from services.embeddings import generate_embedding, event_to_text

    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT event_id, tenant_id, agent_id, event_type, data, retry_count
        FROM events
        WHERE index_status IN ('pending', 'failed')
          AND (next_retry_at IS NULL OR next_retry_at <= NOW())
        ORDER BY timestamp ASC
        LIMIT $1
        """,
        limit,
    )
    processed = 0
    for row in rows:
        event_id = str(row["event_id"])
        data = row["data"]
        if isinstance(data, str):
            data = json.loads(data)
        try:
            text = event_to_text(row["event_type"], dict(data))
            embedding = await generate_embedding(text, str(row["tenant_id"]))
            if not embedding:
                raise RuntimeError("no embedding returned")
            if not await _index_event(pool, row, embedding):
                # Forgotten while the embedding was being generated;
                # do not recreate its vector (#234).
                logger.info("skipping forgotten event %s", event_id)
                continue
            processed += 1
        except Exception as exc:
            await _mark_failed(pool, row, str(exc))
    return processed


async def main() -> None:
    from db.connection import init_db, close_db

    await init_db()
    logger.info("embed worker started (max_retries=%d, backoff_base=%.1fs)",
                MAX_RETRIES, BASE_BACKOFF_SEC)
    try:
        while True:
            n = await process_pending_batch()
            if n:
                logger.info("indexed %d events", n)
            await asyncio.sleep(POLL_INTERVAL)
    finally:
        await close_db()


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    asyncio.run(main())
