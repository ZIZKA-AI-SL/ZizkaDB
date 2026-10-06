"""Shared event write path for API and dashboard test pings."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from typing import Any
from uuid import UUID

from db.connection import get_pool, get_qdrant
from qdrant_client.models import PointStruct
from services.embeddings import generate_embedding, event_to_text
from services.entitlements import embeddings_enabled
from services.exceptions import bad_request

logger = logging.getLogger(__name__)


def _pgvector_literal(embedding: list[float]) -> str:
    """asyncpg has no built-in codec for pgvector's `vector` type, so a raw
    list param fails with 'expected str, got list'. pgvector accepts its
    text input format (e.g. "[0.1,0.2]") cast via `::vector` instead."""
    return "[" + ",".join(repr(x) for x in embedding) + "]"


def _check_value(value: Any, where: str) -> None:
    """Reject values Postgres cannot store, before any row is touched.

    ``json.dumps`` happily emits ``NaN``/``Infinity`` and ``\\u0000``, but
    ``jsonb`` and ``text`` columns refuse them, which used to surface as a 500
    *after* the agent counter had already been bumped.
    """
    if isinstance(value, str):
        if "\x00" in value:
            raise bad_request(f"{where} must not contain NUL (\\u0000) characters")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise bad_request(f"{where} contains an unpaired UTF-16 surrogate")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise bad_request(f"{where} must not contain NaN or Infinity")
    elif isinstance(value, dict):
        for k, v in value.items():
            _check_value(k, f"{where} key")
            _check_value(v, f"{where}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _check_value(v, f"{where}[{i}]")


def validate_event_payload(
    *,
    agent: str,
    event: str,
    data: dict,
    parent_id: str | None,
    session_id: str | None,
    metadata: dict | None,
) -> None:
    """Raise a 400 for input that would otherwise fail mid-write with a 500."""
    _check_value(agent, "agent")
    _check_value(event, "event")
    _check_value(data, "data")
    _check_value(session_id, "session_id")
    _check_value(metadata, "metadata")
    if parent_id is not None:
        try:
            UUID(parent_id)
        except (ValueError, AttributeError, TypeError):
            raise bad_request("parent_id must be a valid event UUID")


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
    validate_event_payload(
        agent=agent,
        event=event,
        data=data,
        parent_id=parent_id,
        session_id=session_id,
        metadata=metadata,
    )

    pool = get_pool()

    content = json.dumps({"event": event, "data": data}, sort_keys=True)
    checksum = hashlib.sha256(content.encode()).hexdigest()

    embed_on = embeddings_enabled()
    initial_status = "pending" if embed_on else "skipped"

    # The agent counter and the event row commit together: if the INSERT
    # fails, event_count must not drift and no phantom agent may be created.
    async with pool.acquire() as conn:
        async with conn.transaction():
            if parent_id:
                parent_tenant_id = await conn.fetchval(
                    "SELECT tenant_id FROM events WHERE event_id = $1",
                    parent_id,
                )
                if parent_tenant_id is None or str(parent_tenant_id) != str(tenant_id):
                    raise bad_request(
                        f"parent_id '{parent_id}' does not exist or belongs to a different tenant"
                    )

            await conn.execute(
                """
                INSERT INTO agents (agent_id, tenant_id, event_count)
                VALUES ($1, $2, 1)
                ON CONFLICT (agent_id, tenant_id)
                DO UPDATE SET last_seen = NOW(), event_count = agents.event_count + 1
                """,
                agent,
                tenant_id,
            )

            row = await conn.fetchrow(
                """
                INSERT INTO events (
                    tenant_id, agent_id, event_type, data,
                    parent_event_id, session_id, checksum, metadata, index_status
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING event_id, timestamp, sequence_no
                """,
                tenant_id,
                agent,
                event,
                json.dumps(data),
                parent_id,
                session_id,
                checksum,
                json.dumps(metadata) if metadata else None,
                initial_status,
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
