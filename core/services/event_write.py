"""Shared event write path for API and dashboard test pings."""

from __future__ import annotations

import json
import logging
import math
import os
from typing import Any
from uuid import UUID

from db.connection import get_pool, get_qdrant
from qdrant_client.models import PointStruct
from services.embeddings import generate_embedding, event_to_text
from services.agent_limits import assert_agent_slot
from services.checksum import compute_row_checksum_v2
from services.entitlements import embeddings_enabled
from services.exceptions import bad_request

logger = logging.getLogger(__name__)

# events.event_type is VARCHAR(100), while the request model allows 255 chars.
EVENT_TYPE_MAX_LENGTH = 100
# events.session_id is VARCHAR(255); the request model puts no cap on it.
SESSION_ID_MAX_LENGTH = 255


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
    if len(event) > EVENT_TYPE_MAX_LENGTH:
        raise bad_request(f"event must be at most {EVENT_TYPE_MAX_LENGTH} characters")
    _check_value(data, "data")
    _check_value(session_id, "session_id")
    if session_id is not None and len(session_id) > SESSION_ID_MAX_LENGTH:
        raise bad_request(f"session_id must be at most {SESSION_ID_MAX_LENGTH} characters")
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

    embed_on = embeddings_enabled()
    initial_status = "pending" if embed_on else "skipped"

    data_json = json.dumps(data)
    metadata_json = json.dumps(metadata) if metadata else None

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

            # A new agent name creates an agent: check the plan has room for it.
            # Inside the transaction so a failed event insert leaves no agent row.
            agent_exists = await conn.fetchval(
                "SELECT 1 FROM agents WHERE tenant_id = $1 AND agent_id = $2",
                tenant_id,
                agent,
            )
            if not agent_exists:
                await assert_agent_slot(conn, tenant_id=tenant_id, agent_id=agent)

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

            # The v2 checksum covers timestamp and sequence_no, so take both
            # from Postgres up front, along with the JSONB / UUID values in the
            # canonical form the verifier will read back, then insert the row
            # once with its checksum. No second write per event, and no reader
            # ever sees an event without its checksum. Run on `conn` so a
            # failed insert rolls back with the agent row; the burnt
            # sequence_no is harmless, /v1/events/verify tolerates gaps.
            canonical = await conn.fetchrow(
                """
                SELECT nextval(pg_get_serial_sequence('events', 'sequence_no')) AS sequence_no,
                       now() AS timestamp,
                       $1::jsonb AS data,
                       $2::jsonb AS metadata,
                       $3::uuid AS parent_event_id
                """,
                data_json,
                metadata_json,
                parent_id,
            )
            checksum = compute_row_checksum_v2({
                "tenant_id": tenant_id,
                "agent_id": agent,
                "event_type": event,
                "data": canonical["data"],
                "parent_event_id": canonical["parent_event_id"],
                "session_id": session_id,
                "timestamp": canonical["timestamp"],
                "sequence_no": canonical["sequence_no"],
                "metadata": canonical["metadata"],
            })

            row = await conn.fetchrow(
                """
                INSERT INTO events (
                    tenant_id, agent_id, event_type, data, parent_event_id, session_id,
                    metadata, index_status, sequence_no, timestamp, checksum
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING event_id, timestamp, sequence_no
                """,
                tenant_id,
                agent,
                event,
                data_json,
                canonical["parent_event_id"],
                session_id,
                metadata_json,
                initial_status,
                canonical["sequence_no"],
                canonical["timestamp"],
                checksum,
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
