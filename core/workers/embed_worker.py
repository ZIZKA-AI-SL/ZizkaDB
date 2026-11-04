#!/usr/bin/env python3
"""Background worker — embed pending events and upsert to Qdrant."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("embed_worker")

POLL_INTERVAL = float(os.getenv("EMBED_WORKER_POLL_SEC", "2"))
# A failed event is retried after RETRY_BASE_SEC, doubling each time up to
# RETRY_MAX_SEC, and marked 'dead' after MAX_ATTEMPTS (defaults: about 3 hours
# of retries, so a provider outage recovers on its own). Re-queue failed and
# dead events with --requeue (see REQUEUE_SQL).
MAX_ATTEMPTS = max(1, int(os.getenv("EMBED_MAX_ATTEMPTS", "10")))
RETRY_BASE_SEC = max(1.0, float(os.getenv("EMBED_RETRY_BASE_SEC", "30")))
RETRY_MAX_SEC = max(RETRY_BASE_SEC, float(os.getenv("EMBED_RETRY_MAX_SEC", "3600")))


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
                "UPDATE events SET embedding = $1::vector, index_status = 'indexed', "
                "index_attempts = 0, index_retry_at = NULL WHERE event_id = $2",
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


# Re-queue failed and dead events with a fresh retry budget ('skipped' events
# were written with embeddings off and are left alone). Run in the container:
#   docker exec zizkadb_embed_worker python workers/embed_worker.py --requeue
REQUEUE_SQL = """
    UPDATE events
    SET index_status = 'pending', index_attempts = 0, index_retry_at = NULL
    WHERE index_status IN ('failed', 'pending', 'dead')
"""

# Pause after a whole batch fails (e.g. Postgres down): doubles per consecutive
# failure, capped, so an outage does not log a traceback every poll.
LOOP_BACKOFF_MAX_SEC = 60.0


async def _record_failure(pool, event_id) -> None:
    """Count the attempt and schedule the retry, or give up after MAX_ATTEMPTS."""
    status = await pool.fetchval(
        """
        UPDATE events
        SET index_attempts = index_attempts + 1,
            index_status = CASE
                WHEN index_attempts + 1 >= $2 THEN 'dead' ELSE 'failed'
            END,
            index_retry_at = CASE
                WHEN index_attempts + 1 >= $2 THEN NULL
                ELSE NOW() + make_interval(
                    secs => LEAST($4::float8, $3::float8 * power(2, LEAST(index_attempts, 30)))
                )
            END
        WHERE event_id = $1
        RETURNING index_status
        """,
        event_id,
        MAX_ATTEMPTS,
        RETRY_BASE_SEC,
        RETRY_MAX_SEC,
    )
    if status == "dead":
        logger.error(
            "giving up on event %s after %d attempts (index_status='dead')",
            event_id,
            MAX_ATTEMPTS,
        )


async def process_pending_batch(limit: int = 50) -> int:
    from db.connection import get_pool
    from services.embeddings import generate_embedding, event_to_text

    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT event_id, tenant_id, agent_id, event_type, data
        FROM events
        WHERE index_status IN ('pending', 'failed')
          AND (index_retry_at IS NULL OR index_retry_at <= NOW())
        ORDER BY timestamp ASC
        LIMIT $1
        """,
        limit,
    )
    processed = 0
    for row in rows:
        event_id = str(row["event_id"])
        try:
            data = row["data"]
            if isinstance(data, str):
                data = json.loads(data)
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
            logger.warning("index failed for %s: %s", event_id, exc)
            await _record_failure(pool, row["event_id"])
    return processed


async def main() -> None:
    from db.connection import init_db, close_db

    await init_db()
    logger.info("embed worker started")
    consecutive_failures = 0
    try:
        while True:
            try:
                n = await process_pending_batch()
                if n:
                    logger.info("indexed %d events", n)
                if consecutive_failures:
                    logger.info("embed worker recovered after %d failed batches", consecutive_failures)
                consecutive_failures = 0
                delay = POLL_INTERVAL
            except Exception as exc:
                # e.g. Postgres briefly unreachable: keep the worker alive and
                # retry, backing off so an outage does not flood the log.
                consecutive_failures += 1
                delay = min(LOOP_BACKOFF_MAX_SEC, POLL_INTERVAL * 2 ** consecutive_failures)
                if consecutive_failures == 1:
                    logger.exception("embed batch failed; retrying in %.0fs", delay)
                else:
                    logger.warning(
                        "embed batch failed again (%d in a row): %s; retrying in %.0fs",
                        consecutive_failures, exc, delay,
                    )
            await asyncio.sleep(delay)
    finally:
        await close_db()


async def requeue() -> str:
    from db.connection import close_db, init_db, get_pool

    await init_db()
    try:
        return await get_pool().execute(REQUEUE_SQL)
    finally:
        await close_db()


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if "--requeue" in sys.argv[1:]:
        print(asyncio.run(requeue()))
    else:
        asyncio.run(main())
