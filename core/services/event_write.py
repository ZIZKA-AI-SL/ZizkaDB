"""Shared event write path for API and dashboard test pings."""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from db.connection import get_pool, get_qdrant
from qdrant_client.models import PointStruct
from services.embeddings import generate_embedding, event_to_text
from services.agent_ids import AGENT_ID_RULE, is_valid_agent_id
from services.agent_limits import assert_agent_slot
from services.audit_chain import compute_chain_hash, latest_link, record_head_break
from services.checksum import compute_row_checksum_v2
from services.entitlements import embeddings_enabled
from services.exceptions import bad_request, payload_too_large
from services.redaction import redact

logger = logging.getLogger(__name__)

# events.event_type is VARCHAR(100), while the request model allows 255 chars.
EVENT_TYPE_MAX_LENGTH = 100
# events.session_id is VARCHAR(255); the request model puts no cap on it.
SESSION_ID_MAX_LENGTH = 255
IDEMPOTENCY_KEY_MAX_LENGTH = 255
# Upper bound for one event's data + metadata as stored JSON. One runaway agent
# loop must not be able to fill the disk with a few huge rows.
MAX_EVENT_BYTES = int(os.getenv("MAX_EVENT_BYTES", str(256 * 1024)))


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
    idempotency_key: str | None = None,
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
    _check_value(idempotency_key, "idempotency_key")
    if idempotency_key is not None and not (
        0 < len(idempotency_key) <= IDEMPOTENCY_KEY_MAX_LENGTH
    ):
        raise bad_request(
            f"idempotency_key must be 1-{IDEMPOTENCY_KEY_MAX_LENGTH} characters"
        )
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
    idempotency_key: str | None = None,
    occurred_at: datetime | None = None,
    supersedes: str | None = None,
    importance: float | None = None,
) -> dict:
    """Write one event. ``occurred_at`` is for imports only: it keeps the
    original timestamp instead of the database clock. ``supersedes`` and
    ``importance`` are stored in metadata (so the checksum covers them) and
    steer context_for() / search (services/memory_rank.py)."""
    metadata = _memory_metadata(metadata, supersedes, importance)
    validate_event_payload(
        agent=agent,
        event=event,
        data=data,
        parent_id=parent_id,
        session_id=session_id,
        metadata=metadata,
        idempotency_key=idempotency_key,
    )

    # Before the size check and the checksum: the original never gets stored.
    data, redacted = redact(data)
    if metadata:
        metadata, redacted_meta = redact(metadata)
        redacted += redacted_meta

    data_json = json.dumps(data)
    metadata_json = json.dumps(metadata) if metadata else None
    size = len(data_json.encode()) + len((metadata_json or "").encode())
    if size > MAX_EVENT_BYTES:
        raise payload_too_large(
            f"Event data and metadata are {size} bytes; the limit is "
            f"{MAX_EVENT_BYTES} (MAX_EVENT_BYTES). Store large blobs elsewhere "
            "and log a reference."
        )

    pool = get_pool()

    if supersedes is not None and not await pool.fetchval(
        "SELECT 1 FROM events WHERE event_id = $1 AND tenant_id = $2 AND agent_id = $3",
        metadata["supersedes"], tenant_id, agent,
    ):
        raise bad_request("supersedes must be the id of an earlier event of the same agent")

    if idempotency_key:
        existing = await _find_idempotent(pool, tenant_id, agent, idempotency_key)
        if existing:
            return existing

    embed_on = embeddings_enabled()
    initial_status = "pending" if embed_on else "skipped"

    try:
        row, checksum = await _insert_event(
            pool,
            tenant_id=tenant_id,
            agent=agent,
            event=event,
            data_json=data_json,
            metadata_json=metadata_json,
            parent_id=parent_id,
            session_id=session_id,
            initial_status=initial_status,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
        )
    except asyncpg.UniqueViolationError:
        # A concurrent retry with the same key won the race; answer with its event.
        existing = await _find_idempotent(pool, tenant_id, agent, idempotency_key)
        if existing:
            return existing
        raise

    result = await _after_insert(
        pool,
        row=row,
        checksum=checksum,
        tenant_id=tenant_id,
        agent=agent,
        event=event,
        data=data,
        embed_on=embed_on,
        initial_status=initial_status,
    )
    if redacted:
        result["redacted"] = redacted
    return result


def _memory_metadata(metadata: dict | None, supersedes: str | None,
                     importance: float | None) -> dict | None:
    if supersedes is None and importance is None:
        return metadata
    metadata = dict(metadata or {})
    if supersedes is not None:
        try:
            metadata["supersedes"] = str(UUID(str(supersedes)))
        except ValueError:
            raise bad_request("supersedes must be a valid event UUID")
    if importance is not None:
        if isinstance(importance, bool) or not 0 <= importance <= 1:
            raise bad_request("importance must be a number from 0 to 1")
        metadata["importance"] = importance
    return metadata


async def _find_idempotent(pool, tenant_id: str, agent: str, key: str | None) -> dict | None:
    if not key:
        return None
    row = await pool.fetchrow(
        """
        SELECT event_id, timestamp, sequence_no, checksum, chain_hash, index_status
        FROM events
        WHERE tenant_id = $1 AND agent_id = $2 AND idempotency_key = $3
        """,
        tenant_id,
        agent,
        key,
    )
    if not row:
        return None
    return {
        "event_id": str(row["event_id"]),
        "timestamp": row["timestamp"].isoformat(),
        "sequence_no": row["sequence_no"],
        "checksum": row["checksum"],
        "chain_hash": row.get("chain_hash"),
        "indexed": row["index_status"] == "indexed",
        "index_status": row["index_status"],
        "duplicate": True,
    }


async def _insert_event(
    pool,
    *,
    tenant_id: str,
    agent: str,
    event: str,
    data_json: str,
    metadata_json: str | None,
    parent_id: str | None,
    session_id: str | None,
    initial_status: str,
    idempotency_key: str | None,
    occurred_at: datetime | None = None,
):

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
                # A new agent must be reachable through /v1/agents/{id}. Agents
                # stored before this rule existed keep accepting events.
                if not is_valid_agent_id(agent):
                    raise bad_request(f"agent must be {AGENT_ID_RULE}")
                await assert_agent_slot(conn, tenant_id=tenant_id, agent_id=agent)

            # Returns the head this agent recorded so far (the upsert does not
            # change it), checked against the chain below.
            stored_head = await conn.fetchrow(
                """
                INSERT INTO agents (agent_id, tenant_id, event_count)
                VALUES ($1, $2, 1)
                ON CONFLICT (agent_id, tenant_id)
                DO UPDATE SET last_seen = NOW(), event_count = agents.event_count + 1
                RETURNING chain_head_seq, chain_head_hash
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
                       COALESCE($4::timestamptz, now()) AS timestamp,
                       $1::jsonb AS data,
                       $2::jsonb AS metadata,
                       $3::uuid AS parent_event_id
                """,
                data_json,
                metadata_json,
                parent_id,
                occurred_at,
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
            # The agents upsert above holds this agent's row lock until commit,
            # so no other write for the agent can take the same previous link.
            # If the newest events were removed outside an erasure, this event
            # would link past the gap: keep the evidence first.
            latest = await latest_link(conn, tenant_id, agent)
            await record_head_break(conn, tenant_id, agent, stored_head, latest)
            chain_hash = compute_chain_hash(latest[1] if latest else None, checksum)

            # The same statement records the agent's newest link for the
            # verifier, on the row the upsert above already holds. Only a real
            # insert gets there: an idempotent duplicate returns before the
            # transaction or rolls it back. The head is never read to compute
            # a link.
            row = await conn.fetchrow(
                """
                WITH ins AS (
                    INSERT INTO events (
                        tenant_id, agent_id, event_type, data, parent_event_id, session_id,
                        metadata, index_status, sequence_no, timestamp, checksum,
                        idempotency_key, chain_hash
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                    RETURNING event_id, timestamp, sequence_no, chain_hash
                ), head AS (
                    UPDATE agents
                    SET chain_head_seq = ins.sequence_no, chain_head_hash = ins.chain_hash
                    FROM ins
                    WHERE agents.tenant_id = $1 AND agents.agent_id = $2
                )
                SELECT event_id, timestamp, sequence_no, chain_hash FROM ins
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
                idempotency_key,
                chain_hash,
            )
    return row, checksum


async def _after_insert(
    pool,
    *,
    row,
    checksum: str,
    tenant_id: str,
    agent: str,
    event: str,
    data: dict,
    embed_on: bool,
    initial_status: str,
) -> dict:
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
        "chain_hash": row.get("chain_hash"),
        "indexed": indexed,
        "index_status": index_status,
        "duplicate": False,
    }
