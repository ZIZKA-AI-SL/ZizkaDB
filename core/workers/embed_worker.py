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


async def process_pending_batch(limit: int = 50) -> int:
    from db.connection import init_db, close_db, get_pool
    from services.embeddings import generate_embedding, event_to_text

    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT event_id, tenant_id, agent_id, event_type, data
        FROM events
        WHERE index_status IN ('pending', 'failed')
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
            logger.warning("index failed for %s: %s", event_id, exc)
            await pool.execute(
                "UPDATE events SET index_status = 'failed' WHERE event_id = $1",
                row["event_id"],
            )
    return processed


async def main() -> None:
    from db.connection import init_db, close_db

    await init_db()
    logger.info("embed worker started")
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
