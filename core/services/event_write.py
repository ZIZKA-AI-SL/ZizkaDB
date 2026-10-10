"""Shared event write path for API and dashboard test pings."""

from __future__ import annotations

import json
import logging
import os

from db.connection import get_pool, get_qdrant
from qdrant_client.models import PointStruct
from services.checksum import compute_event_checksum_v2
from services.embeddings import generate_embedding, event_to_text
from services.entitlements import embeddings_enabled
from services.exceptions import bad_request

logger = logging.getLogger(__name__)


def _pgvector_literal(embedding: list[float]) -> str:
    """asyncpg has no built-in codec for pgvector's `vector` type, so a raw
    list param fails with 'expected str, got list'. pgvector accepts its
    text input format (e.g. "[0.1,0.2]") cast via `::vector` instead."""
    return "[" + ",".join(repr(x) for x in embedding) + "]"


async def write_event(
    *,
    tenant_id: str,
    agent: str,
    event: str,
    data: dict,
    parent_id: str | None = None,
    session_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    pool = get_pool()

    if parent_id:
        parent_tenant_id = await pool.fetchval(
            "SELECT tenant_id FROM events WHERE event_id = $1",
            parent_id,
        )
        if parent_tenant_id is None or str(parent_tenant_id) != str(tenant_id):
            raise bad_request(
                f"parent_id '{parent_id}' does not exist or belongs to a different tenant"
            )

    await pool.execute(
        """
        INSERT INTO agents (agent_id, tenant_id)
        VALUES ($1, $2)
        ON CONFLICT (agent_id, tenant_id)
        DO UPDATE SET last_seen = NOW(), event_count = agents.event_count + 1
        """,
        agent,
        tenant_id,
    )

    embed_on = embeddings_enabled()
    initial_status = "pending" if embed_on else "skipped"

    # The v2 checksum covers timestamp and sequence_no, which only exist
    # after INSERT, so it is written in the same transaction as the insert —
    # no reader ever observes an event without its checksum.
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO events (
                    tenant_id, agent_id, event_type, data,
                    parent_event_id, session_id, metadata, index_status
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING event_id, timestamp, sequence_no
                """,
                tenant_id,
                agent,
                event,
                json.dumps(data),
                parent_id,
                session_id,
                json.dumps(metadata) if metadata else None,
                initial_status,
            )
            checksum = compute_event_checksum_v2(
                tenant_id=tenant_id,
                agent_id=agent,
                event_type=event,
                data=data,
                parent_event_id=parent_id,
                session_id=session_id,
                timestamp=row["timestamp"],
                sequence_no=row["sequence_no"],
                metadata=metadata,
            )
            await conn.execute(
                "UPDATE events SET checksum = $1 WHERE event_id = $2",
                checksum,
                row["event_id"],
            )

    event_id = str(row["event_id"])

    indexed = False
    index_status = initial_status
    if embed_on and os.getenv("EMBED_SYNC", "false").lower() in ("1", "true", "yes"):
        try:
            text = event_to_text(event, data)
            embedding = await generate_embedding(text, tenant_id)
            if embedding:
                await pool.execute(
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
                                "tenant_id": tenant_id,
                                "agent_id": agent,
                                "event_type": event,
                                "timestamp": row["timestamp"].isoformat(),
                            },
                        )
                    ],
                )
                indexed = True
                index_status = "indexed"
        except Exception as e:
            logger.warning("Embedding/index skipped for event %s: %s", event_id, e)
            await pool.execute(
                "UPDATE events SET index_status = 'failed' WHERE event_id = $1",
                row["event_id"],
            )
            index_status = "failed"

    try:
        await pool.execute(
            """
            INSERT INTO usage_daily (tenant_id, date, events_written)
            VALUES ($1, CURRENT_DATE, 1)
            ON CONFLICT (tenant_id, date)
            DO UPDATE SET events_written = usage_daily.events_written + 1
            """,
            tenant_id,
        )
    except Exception as e:
        logger.warning("usage_daily meter skipped for tenant %s: %s", tenant_id, e)

    return {
        "event_id": event_id,
        "timestamp": row["timestamp"].isoformat(),
        "sequence_no": row["sequence_no"],
        "checksum": checksum,
        "indexed": indexed,
        "index_status": index_status,
    }
