"""Checksum computation and verification for event audit integrity.

ZizkaDB's tamper-evidence guarantee (README, EU AI Act Article 12) depends on
each event's ``checksum`` covering everything an auditor cares about. Version 2
of the checksum covers all immutable audit fields — tenant, agent, causal link,
session, timestamp and sequence number — not just the event payload. Rows
written before this change carry version-1 checksums (payload only); the
verifier recognises both so historical data keeps validating, and reports
which version matched so auditors know the coverage of each result. v1 is
only accepted for rows written before v2 (``checksum_v2_cutoff``), so a v2 row
cannot be downgraded to the weaker payload-only hash.

Threat model: the checksum is an unkeyed SHA-256 stored in the same row. It
catches edits made without recomputing it (manual fixes, buggy migrations,
partial tampering), not a writer who recomputes it, and it cannot reveal a
deleted row.

The write path has Postgres canonicalise the values first (``$n::jsonb``,
``$n::uuid``, ``nextval``, ``now()``) and hashes those through
:func:`compute_row_checksum_v2`, the same function the verifier uses, so both
sides hash the database's canonical form (JSONB-normalised numbers, canonical
UUIDs, NULL for empty metadata) rather than the raw request values.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID

CHECKSUM_V2 = "v2"
CHECKSUM_V1 = "v1"

# Columns every v2 computation / verification needs from the events table.
CHECKSUM_COLUMNS = (
    "event_id, tenant_id, agent_id, timestamp, event_type, "
    "data, parent_event_id, session_id, sequence_no, metadata, checksum"
)


def _canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators, raw unicode."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _uuid_str(value: Any) -> str | None:
    """Canonical lowercase hyphenated UUID, matching what Postgres returns."""
    if value is None or value == "":
        return None
    try:
        return str(value if isinstance(value, UUID) else UUID(str(value)))
    except ValueError:
        return str(value)


def _isoformat_utc(value: datetime) -> str:
    """Canonical UTC timestamp; naive datetimes are assumed to be UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _json_value(value: Any) -> Any:
    """JSONB columns come back from asyncpg as text unless a codec is set."""
    if isinstance(value, str):
        return json.loads(value)
    return value


def compute_event_checksum_v2(
    *,
    tenant_id: Any,
    agent_id: str,
    event_type: str,
    data: dict,
    parent_event_id: Any = None,
    session_id: str | None = None,
    timestamp: datetime,
    sequence_no: int,
    metadata: dict | None = None,
) -> str:
    """v2 checksum: covers every immutable audit field on the event row.

    ``embedding`` and ``index_status`` are deliberately excluded — they are
    updated in place by the embedding pipeline after insert and must not
    invalidate the audit checksum.
    """
    preimage = {
        "v": 2,
        "tenant_id": _uuid_str(tenant_id),
        "agent_id": agent_id,
        "event_type": event_type,
        "data": data,
        "parent_event_id": _uuid_str(parent_event_id),
        "session_id": session_id,
        "timestamp": _isoformat_utc(timestamp),
        "sequence_no": int(sequence_no),
        # The write path stores empty metadata as NULL.
        "metadata": metadata or None,
    }
    return hashlib.sha256(_canonical_json(preimage).encode("utf-8")).hexdigest()


def compute_row_checksum_v2(row: Mapping[str, Any]) -> str:
    """v2 checksum of an events row as read from (or returned by) Postgres."""
    return compute_event_checksum_v2(
        tenant_id=row["tenant_id"],
        agent_id=row["agent_id"],
        event_type=row["event_type"],
        data=_json_value(row["data"]),
        parent_event_id=row["parent_event_id"],
        session_id=row["session_id"],
        timestamp=row["timestamp"],
        sequence_no=row["sequence_no"],
        metadata=_json_value(row["metadata"]),
    )


def compute_legacy_checksum(event: str, data: dict) -> str:
    """Version-1 checksum: exactly the historical formula (payload only).

    Kept byte-for-byte identical to the pre-v2 write path so rows written
    before this change verify as ``valid_legacy`` rather than ``mismatch``.
    """
    content = json.dumps({"event": event, "data": data}, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()


async def fetch_legacy_cutoff(conn) -> int | None:
    """Highest ``sequence_no`` that may still carry a v1 checksum.

    Recorded once, by ``init_db()``, when the database first runs v2 code.
    ``None`` (no row) means no event may verify as legacy.
    """
    return await conn.fetchval("SELECT legacy_until_seq FROM checksum_v2_cutoff")


def verify_stored_checksum(row: Mapping[str, Any], legacy_until_seq: int | None) -> dict:
    """Recompute a row's checksum and compare against the stored value.

    Returns ``{status, version}`` where status is one of ``valid`` (v2 match),
    ``valid_legacy`` (v1 match), ``mismatch`` or ``missing`` (no checksum
    stored). The hashes are not returned: the recomputed value is exactly what
    someone would need to write back to hide an edit.

    v1 is only accepted for rows at or below ``legacy_until_seq``. Without that
    bound, anyone could edit a v2-only field (agent, session, parent, metadata,
    ...) and replace the checksum with the payload-only v1 hash, which does not
    change, to get ``valid_legacy``.
    """
    stored = row["checksum"]
    if not stored:
        return {"status": "missing", "version": None}

    if stored == compute_row_checksum_v2(row):
        return {"status": "valid", "version": CHECKSUM_V2}

    legacy_allowed = legacy_until_seq is not None and int(row["sequence_no"]) <= legacy_until_seq
    if legacy_allowed and stored == compute_legacy_checksum(
        row["event_type"], _json_value(row["data"])
    ):
        return {"status": "valid_legacy", "version": CHECKSUM_V1}

    return {"status": "mismatch", "version": None}
