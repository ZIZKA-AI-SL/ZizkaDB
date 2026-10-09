"""Per-tenant hash chain over the events table.

Each event gets ``chain_hash = H(prev_chain_hash, canonical record)``, where the
record covers every audit-relevant column (agent, type, data, metadata, causal
parent, session, timestamp, sequence number). Editing any of them, deleting an
event, or reordering events breaks the chain at that point, and
``verify_entries`` reports the first broken link.

``H`` is HMAC-SHA256 when ``AUDIT_CHAIN_KEY`` is set and plain SHA-256
otherwise. Without a key the chain still exposes edits made by anyone who does
not recompute every later hash; with a key kept outside the database, even a
party with full database write access cannot forge a valid chain.

Lawful erasure (GDPR forget, agent delete) records a tombstone in
``event_erasures`` that keeps the erased event's chain hashes but none of its
content, so the chain stays verifiable and the gap is reported as erased rather
than tampered.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

CHAIN_VERSION = 1

# Columns selected for hashing. ``data::text`` / ``metadata::text`` use
# PostgreSQL's own jsonb text form, which is canonical (key order, spacing,
# number formatting), so write-time and verify-time hashes agree.
RECORD_COLUMNS = """
    event_id, tenant_id, agent_id, timestamp, event_type,
    data::text AS data_text, metadata::text AS metadata_text,
    parent_event_id, session_id, sequence_no
"""


def _key() -> bytes | None:
    key = os.getenv("AUDIT_CHAIN_KEY", "")
    return key.encode() if key else None


def _str(value: Any) -> str | None:
    return None if value is None else str(value)


def canonical_record(row: Any) -> str:
    """Serialize the audit-relevant columns of one event deterministically."""
    ts = row["timestamp"]
    return json.dumps(
        [
            CHAIN_VERSION,
            _str(row["tenant_id"]),
            _str(row["event_id"]),
            int(row["sequence_no"]),
            ts.isoformat() if ts is not None else None,
            row["agent_id"],
            row["event_type"],
            row["data_text"],
            row["metadata_text"],
            _str(row["parent_event_id"]),
            row["session_id"],
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    )


def link_hash(prev_chain_hash: str | None, row: Any) -> str:
    message = ((prev_chain_hash or "") + "\n" + canonical_record(row)).encode()
    key = _key()
    if key:
        return hmac.new(key, message, hashlib.sha256).hexdigest()
    return hashlib.sha256(message).hexdigest()


async def lock_tenant_chain(conn, tenant_id: str) -> None:
    """Serialize chain appends per tenant for the rest of the transaction."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", str(tenant_id))


async def chain_head(conn, tenant_id: str) -> str | None:
    """Latest chain hash for the tenant, including erased events."""
    return await conn.fetchval(
        """
        SELECT chain_hash FROM (
            SELECT sequence_no, chain_hash FROM events
             WHERE tenant_id = $1 AND chain_hash IS NOT NULL
            UNION ALL
            SELECT sequence_no, chain_hash FROM event_erasures
             WHERE tenant_id = $1
        ) links
        ORDER BY sequence_no DESC
        LIMIT 1
        """,
        tenant_id,
    )


async def append_event(conn, tenant_id: str, event_id) -> str:
    """Chain a freshly inserted event. Caller holds ``lock_tenant_chain``."""
    prev = await chain_head(conn, tenant_id)
    row = await conn.fetchrow(
        f"SELECT {RECORD_COLUMNS} FROM events WHERE event_id = $1",
        event_id,
    )
    chain_hash = link_hash(prev, row)
    await conn.execute(
        "UPDATE events SET chain_hash = $1, prev_chain_hash = $2 WHERE event_id = $3",
        chain_hash,
        prev,
        event_id,
    )
    return chain_hash


async def record_erasures(conn, tenant_id: str, where_sql: str, *args, reason: str) -> None:
    """Tombstone chained events that are about to be deleted.

    ``where_sql`` is an extra condition on ``events`` using ``$2..$n`` for
    ``args`` (``$1`` is the tenant id).
    """
    await conn.execute(
        f"""
        INSERT INTO event_erasures
            (event_id, tenant_id, sequence_no, chain_hash, prev_chain_hash, reason)
        SELECT event_id, tenant_id, sequence_no, chain_hash, prev_chain_hash, ${len(args) + 2}
        FROM events
        WHERE tenant_id = $1 AND chain_hash IS NOT NULL AND ({where_sql})
        ON CONFLICT (event_id) DO NOTHING
        """,
        tenant_id,
        *args,
        reason,
    )


def _relinked_to_erased_parent(prev: str | None, row: dict, erased_ids: list[str]) -> bool:
    """True if ``row`` hashed correctly with a parent that was lawfully erased.

    Erasing an event sets its children's ``parent_event_id`` to NULL
    (ON DELETE SET NULL). That is the only accepted way for a link to change:
    the original parent must be one of the tombstoned events earlier in the chain.
    """
    return any(
        link_hash(prev, {**row, "parent_event_id": pid}) == row["chain_hash"]
        for pid in erased_ids
    )


def verify_entries(entries: list[dict]) -> dict:
    """Check a tenant's chain.

    ``entries`` are live events (``RECORD_COLUMNS`` + ``chain_hash`` +
    ``prev_chain_hash``) and tombstones (``erased=True``), in any order.
    Events without a ``chain_hash`` predate the chain and are only counted.
    """
    chained = sorted(
        (e for e in entries if e.get("chain_hash")),
        key=lambda e: int(e["sequence_no"]),
    )
    result = {
        "ok": True,
        "checked": 0,
        "erased": 0,
        "unchained": sum(1 for e in entries if not e.get("chain_hash")),
        "head": None,
        "first_problem": None,
    }
    expected_prev = None
    erased_ids: list[str] = []
    for e in chained:
        problem = None
        if e.get("prev_chain_hash") != expected_prev:
            problem = "missing_or_reordered_link"
        elif not e.get("erased") and link_hash(expected_prev, e) != e["chain_hash"]:
            problem = "content_changed"
            if e["parent_event_id"] is None and _relinked_to_erased_parent(
                expected_prev, e, erased_ids
            ):
                problem = None
        if e.get("erased"):
            erased_ids.append(_str(e["event_id"]))
        if problem:
            result["ok"] = False
            result["first_problem"] = {
                "kind": problem,
                "event_id": _str(e["event_id"]),
                "sequence_no": int(e["sequence_no"]),
            }
            break
        if e.get("erased"):
            result["erased"] += 1
        else:
            result["checked"] += 1
        expected_prev = e["chain_hash"]
    result["head"] = expected_prev
    return result
