"""Per-agent hash chain over event checksums: proves nothing was removed or reordered.

Each event stores ``chain_hash = sha256(prev_chain_hash + ":" + checksum)``,
where ``prev_chain_hash`` is the agent's previous link ("" for the first one).
The checksum (services/checksum.py) covers the row's content; the chain covers
the sequence, so deleting, inserting or reordering a row breaks every later link.

Legitimate deletions (GDPR forget, retention) first copy each deleted row's
``chain_hash`` into ``event_tombstones``. The tombstone keeps the link, not the
content, so the chain still verifies and shows where and why rows were removed.

Writes for one agent are serialised by the ``agents`` row lock the write path
takes (``INSERT ... ON CONFLICT DO UPDATE``), so the previous link read here is
the one the new event follows.

Threat model: like the checksum, the chain is unkeyed. Someone with write
access to the database could rebuild it end to end. Recording the head
(``chain_head``) outside ZizkaDB and passing it back as an anchor detects that.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from services.checksum import CHECKSUM_COLUMNS, verify_stored_checksum

# Reads for verification, in sequence order.
CHAIN_PAGE_SIZE = 2000


def compute_chain_hash(prev_chain_hash: str | None, checksum: str) -> str:
    return hashlib.sha256(f"{prev_chain_hash or ''}:{checksum}".encode()).hexdigest()


async def previous_link(conn, tenant_id: str, agent_id: str) -> str | None:
    """The agent's latest link: its newest event or tombstone, by sequence_no."""
    return await conn.fetchval(
        """
        SELECT chain_hash FROM (
            (SELECT sequence_no, chain_hash FROM events
             WHERE tenant_id = $1 AND agent_id = $2
             ORDER BY sequence_no DESC LIMIT 1)
            UNION ALL
            (SELECT sequence_no, chain_hash FROM event_tombstones
             WHERE tenant_id = $1 AND agent_id = $2
             ORDER BY sequence_no DESC LIMIT 1)
        ) latest
        ORDER BY sequence_no DESC
        LIMIT 1
        """,
        tenant_id,
        agent_id,
    )


async def tombstone_events(conn, tenant_id: str, event_ids: list, reason: str) -> None:
    """Keep the chain links of events about to be deleted (call in the same transaction)."""
    await conn.execute(
        """
        INSERT INTO event_tombstones (tenant_id, agent_id, event_id, sequence_no, chain_hash, reason)
        SELECT tenant_id, agent_id, event_id, sequence_no, chain_hash, $3
        FROM events
        WHERE tenant_id = $1 AND event_id = ANY($2::uuid[]) AND chain_hash IS NOT NULL
        ON CONFLICT DO NOTHING
        """,
        tenant_id,
        event_ids,
        reason,
    )


async def verify_chain(
    pool,
    tenant_id: str,
    agent_id: str,
    *,
    after_seq: int | None = None,
    anchor: str | None = None,
    legacy_until_seq: int | None = None,
) -> dict:
    """Walk an agent's events and tombstones in order and check every link.

    ``after_seq`` + ``anchor`` resume from a head recorded earlier: the walk
    starts after that sequence number and expects ``anchor`` as the previous link.
    """
    summary = {
        "events": 0,
        "chained": 0,
        "unchained_legacy": 0,
        "tombstones": 0,
        "checksum_mismatch": 0,
        "breaks": 0,
    }
    problems: list[dict[str, Any]] = []
    prev: str | None = anchor
    started = anchor is not None
    head: dict[str, Any] | None = None
    cursor = after_seq or 0

    if anchor is not None and after_seq is not None:
        anchored = await pool.fetchval(
            """
            SELECT 1 FROM events
            WHERE tenant_id = $1 AND agent_id = $2 AND sequence_no = $3 AND chain_hash = $4
            UNION ALL
            SELECT 1 FROM event_tombstones
            WHERE tenant_id = $1 AND agent_id = $2 AND sequence_no = $3 AND chain_hash = $4
            LIMIT 1
            """,
            tenant_id, agent_id, after_seq, anchor,
        )
        if not anchored:
            summary["breaks"] += 1
            problems.append({
                "sequence_no": after_seq,
                "problem": "anchor_mismatch",
                "detail": "No event or tombstone at this sequence number has the anchor hash.",
            })

    while True:
        rows = await pool.fetch(
            f"""
            SELECT 'event' AS kind, {CHECKSUM_COLUMNS}, chain_hash, NULL AS reason
            FROM events
            WHERE tenant_id = $1 AND agent_id = $2 AND sequence_no > $3
            UNION ALL
            SELECT 'tombstone', event_id, tenant_id, agent_id, deleted_at, NULL, NULL,
                   NULL, NULL, sequence_no, NULL, NULL, chain_hash, reason
            FROM event_tombstones
            WHERE tenant_id = $1 AND agent_id = $2 AND sequence_no > $3
            ORDER BY sequence_no
            LIMIT {CHAIN_PAGE_SIZE}
            """,
            tenant_id, agent_id, cursor,
        )
        if not rows:
            break
        for r in rows:
            _check_link(r, summary, problems, legacy_until_seq, prev, started)
            if r["chain_hash"] is not None:
                prev = r["chain_hash"]
                started = True
                head = {"sequence_no": r["sequence_no"], "chain_hash": r["chain_hash"]}
            cursor = r["sequence_no"]
        if len(rows) < CHAIN_PAGE_SIZE:
            break

    return {
        "agent": agent_id,
        "intact": summary["breaks"] == 0 and summary["checksum_mismatch"] == 0,
        "summary": summary,
        "problems": problems[:100],
        "chain_head": head,
    }


def _check_link(
    r: Mapping[str, Any],
    summary: dict,
    problems: list,
    legacy_until_seq: int | None,
    prev: str | None,
    started: bool,
) -> None:
    where = {"sequence_no": r["sequence_no"], "event_id": str(r["event_id"])}
    if r["kind"] == "tombstone":
        summary["tombstones"] += 1
        return

    summary["events"] += 1
    status = verify_stored_checksum(r, legacy_until_seq)["status"]
    if status in ("mismatch", "missing"):
        summary["checksum_mismatch"] += 1
        problems.append({**where, "problem": f"checksum_{status}"})

    if r["chain_hash"] is None:
        if started:
            # A row without a link after the chain began was not written by
            # the write path: inserted directly into the table.
            summary["breaks"] += 1
            problems.append({**where, "problem": "unchained_after_chain_start"})
        else:
            summary["unchained_legacy"] += 1
        return

    summary["chained"] += 1
    if r["chain_hash"] != compute_chain_hash(prev, r["checksum"] or ""):
        summary["breaks"] += 1
        problems.append({
            **where,
            "problem": "chain_break",
            "detail": "The link does not follow the previous one: a row before it was "
                      "removed, inserted or changed, or this row's checksum was changed.",
        })
