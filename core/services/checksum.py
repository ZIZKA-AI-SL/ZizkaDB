"""Checksum computation and verification for event audit integrity.

ZizkaDB's tamper-evidence guarantee (README, EU AI Act Article 12) depends on
each event's ``checksum`` covering everything an auditor cares about. Version 2
of the checksum covers all immutable audit fields — tenant, agent, causal link,
session, timestamp and sequence number — not just the event payload. Rows
written before this change carry version-1 checksums (payload only); the
verifier recognises both so historical data keeps validating, and reports
which version matched so auditors know the coverage of each result.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

CHECKSUM_V2 = "v2"
CHECKSUM_V1 = "v1"


def _canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators, raw unicode.

    The same bytes must be produced from a write-time dict and from the dict
    reloaded from the JSONB column — JSONB normalises key order and
    whitespace, so canonicalisation must too.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _isoformat_utc(value: datetime) -> str:
    """Canonical timestamp string for the preimage.

    Postgres TIMESTAMPTZ round-trips through asyncpg as tz-aware UTC; if a
    naive datetime slips in (tests, direct calls) assume UTC rather than
    producing an ambiguous preimage.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


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
        "tenant_id": str(tenant_id),
        "agent_id": agent_id,
        "event_type": event_type,
        "data": data,
        "parent_event_id": str(parent_event_id) if parent_event_id else None,
        "session_id": session_id,
        "timestamp": _isoformat_utc(timestamp),
        "sequence_no": int(sequence_no),
        "metadata": metadata,
    }
    return hashlib.sha256(_canonical_json(preimage).encode("utf-8")).hexdigest()


def compute_legacy_checksum(event: str, data: dict) -> str:
    """Version-1 checksum: exactly the historical formula (payload only).

    Kept byte-for-byte identical to the pre-v2 write path so rows written
    before this change verify as ``valid_legacy`` rather than ``mismatch``.
    """
    content = json.dumps({"event": event, "data": data}, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()


def verify_stored_checksum(row: dict) -> dict:
    """Recompute a row's checksum and compare against the stored value.

    Returns ``{status, version, stored, recomputed}`` where status is one of
    ``valid`` (v2 match), ``valid_legacy`` (v1 match), ``mismatch`` or
    ``missing`` (no checksum stored — e.g. rows written before checksums were
    introduced).
    """
    stored = row.get("checksum")
    data = row.get("data")
    if isinstance(data, str):
        data = json.loads(data)
    metadata = row.get("metadata")
    if isinstance(metadata, str):
        metadata = json.loads(metadata)

    if not stored:
        return {"status": "missing", "version": None, "stored": stored, "recomputed": None}

    v2 = compute_event_checksum_v2(
        tenant_id=row["tenant_id"],
        agent_id=row["agent_id"],
        event_type=row["event_type"],
        data=data,
        parent_event_id=row.get("parent_event_id"),
        session_id=row.get("session_id"),
        timestamp=row["timestamp"],
        sequence_no=row["sequence_no"],
        metadata=metadata,
    )
    if stored == v2:
        return {"status": "valid", "version": CHECKSUM_V2, "stored": stored, "recomputed": v2}

    v1 = compute_legacy_checksum(row["event_type"], data)
    if stored == v1:
        return {
            "status": "valid_legacy",
            "version": CHECKSUM_V1,
            "stored": stored,
            "recomputed": v1,
        }

    return {"status": "mismatch", "version": None, "stored": stored, "recomputed": v2}
