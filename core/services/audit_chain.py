"""Per-agent hash chain over event checksums: proves nothing was removed or reordered.

Each event stores ``chain_hash = sha256(prev_chain_hash + ":" + checksum)``,
where ``prev_chain_hash`` is the agent's previous link ("" for the first one).
The checksum (services/checksum.py) covers the row's content; the chain covers
the sequence, so deleting, inserting or reordering a row breaks every later link.

Legitimate deletions (GDPR forget, retention) copy each deleted row's
``chain_hash`` and ``checksum`` into ``event_tombstones``. The tombstone keeps
the link, not the content, so the chain still verifies and shows where and why
rows were removed. Because it keeps the checksum, the verifier recomputes a
tombstone's link exactly like an event's, so the chain doesn't restart at an
erasure. A trigger fills the checksum when an older image writes the
tombstone, so tombstones stay checkable through a rolling deploy or downgrade.
Tombstones written before the upgrade (0.5.0) have no checksum; they are
accepted only if deleted before ``tombstone_checksum_cutoff.since``, which
``init_db()`` records once the trigger exists.

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


async def lock_events(conn, event_ids: list) -> None:
    """Take each event's advisory lock for this transaction, before deleting it.

    The key is ``hashtext`` of the id, the one the embed worker takes, so it
    cannot re-create a vector for a row being erased. Locks are taken in id
    order, byte-wise (``COLLATE "C"``), which is the order 0.5.0 used
    (``sorted(..., key=str)``): an older and a newer image erasing the same
    events during a rolling deploy lock in the same order and cannot deadlock.
    Two ids sharing a 32-bit key could still interleave badly; Postgres then
    aborts one transaction, which is retried, and that needs two collisions.
    """
    await conn.execute(
        """
        SELECT pg_advisory_xact_lock(hashtext(id))
        FROM (
            SELECT DISTINCT e::text COLLATE "C" AS id
            FROM unnest($1::uuid[]) AS e
            ORDER BY id
        ) ids
        """,
        event_ids,
    )


async def delete_and_tombstone(conn, tenant_id: str, event_ids: list, reason: str) -> int:
    """Delete events, keeping a tombstone for each chained one; returns how many were deleted.

    One statement, so the tombstones and the count come from the rows this call
    actually deleted: an erasure that overlaps another one (which already
    deleted some of the rows) neither tombstones nor counts them twice.
    """
    return await conn.fetchval(
        """
        WITH gone AS (
            DELETE FROM events
            WHERE tenant_id = $1 AND event_id = ANY($2::uuid[])
            RETURNING tenant_id, agent_id, event_id, sequence_no, chain_hash, checksum
        ), kept AS (
            INSERT INTO event_tombstones
                (tenant_id, agent_id, event_id, sequence_no, chain_hash, checksum, reason)
            SELECT tenant_id, agent_id, event_id, sequence_no, chain_hash, checksum, $3
            FROM gone
            WHERE chain_hash IS NOT NULL
            ON CONFLICT DO NOTHING
        )
        SELECT COUNT(*) FROM gone
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
        "legacy_tombstones": 0,
        "stale_head": 0,
        "checksum_mismatch": 0,
        "breaks": 0,
    }
    problems: list[dict[str, Any]] = []
    prev: str | None = anchor
    started = anchor is not None
    head: dict[str, Any] | None = None
    cursor = after_seq or 0
    # Tombstones without a checksum are accepted only from before this point.
    checksum_since = await pool.fetchval("SELECT since FROM tombstone_checksum_cutoff")
    # The recorded head is read before and after the walk, and compared only
    # if it did not change in between: a write, an agent delete or re-create
    # during the check is not evidence of tampering.
    stored_head = await _stored_head(pool, tenant_id, agent_id)
    anchor_ok = True

    if anchor is not None and after_seq is not None:
        if await _link_hash_at(pool, tenant_id, agent_id, after_seq) != anchor:
            anchor_ok = False
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
                   NULL, NULL, sequence_no, NULL, checksum, chain_hash, reason
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
            _check_link(r, summary, problems, legacy_until_seq, prev, started, checksum_since)
            if r["chain_hash"] is not None:
                prev = r["chain_hash"]
                started = True
                head = {"sequence_no": r["sequence_no"], "chain_hash": r["chain_hash"]}
            cursor = r["sequence_no"]
        if len(rows) < CHAIN_PAGE_SIZE:
            break

    last_link = head
    if last_link is None and anchor is not None and after_seq is not None and anchor_ok:
        last_link = {"sequence_no": after_seq, "chain_hash": anchor}
    if anchor_ok and stored_head == await _stored_head(pool, tenant_id, agent_id):
        await _check_stored_head(pool, tenant_id, agent_id, stored_head, last_link, summary, problems)

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
    checksum_since: Any,
) -> None:
    where = {"sequence_no": r["sequence_no"], "event_id": str(r["event_id"])}
    if r["kind"] == "tombstone":
        _check_tombstone(r, where, summary, problems, prev, checksum_since)
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


def _check_tombstone(
    r: Mapping[str, Any],
    where: dict,
    summary: dict,
    problems: list,
    prev: str | None,
    checksum_since: Any,
) -> None:
    """A tombstone's link is checked like an event's, from the checksum it kept."""
    summary["tombstones"] += 1
    if r["checksum"] is None:
        # The timestamp column carries deleted_at for tombstones.
        if checksum_since is None or r["timestamp"] < checksum_since:
            summary["legacy_tombstones"] += 1
        else:
            summary["breaks"] += 1
            problems.append({
                **where,
                "problem": "tombstone_missing_checksum",
                "detail": "This tombstone has no checksum, but every tombstone written "
                          "since the upgrade gets one, so its link cannot be checked.",
            })
        return

    if r["chain_hash"] != compute_chain_hash(prev, r["checksum"]):
        summary["breaks"] += 1
        problems.append({
            **where,
            "problem": "chain_break",
            "detail": "The tombstone's link does not follow the previous one: a row "
                      "before it was removed, inserted or changed, or the tombstone was changed.",
        })


async def _link_hash_at(pool, tenant_id: str, agent_id: str, sequence_no: int) -> str | None:
    """The agent's link at this sequence number, from an event or a tombstone."""
    return await pool.fetchval(
        """
        SELECT chain_hash FROM events
        WHERE tenant_id = $1 AND agent_id = $2 AND sequence_no = $3
        UNION ALL
        SELECT chain_hash FROM event_tombstones
        WHERE tenant_id = $1 AND agent_id = $2 AND sequence_no = $3
        LIMIT 1
        """,
        tenant_id,
        agent_id,
        sequence_no,
    )


async def _stored_head(pool, tenant_id: str, agent_id: str) -> tuple | None:
    row = await pool.fetchrow(
        "SELECT chain_head_seq, chain_head_hash FROM agents WHERE tenant_id = $1 AND agent_id = $2",
        tenant_id,
        agent_id,
    )
    return None if row is None else (row["chain_head_seq"], row["chain_head_hash"])


async def _check_stored_head(
    pool,
    tenant_id: str,
    agent_id: str,
    stored_head: tuple | None,
    last_link: Mapping[str, Any] | None,
    summary: dict,
    problems: list,
) -> None:
    """Compare the head the write path recorded with the last link walked.

    A head that points to an existing link with the same hash, but not the
    last one, is stale: an older image (which does not record heads) wrote
    after it. That is not a break, but on a deployment that never ran an older
    image it means the head was moved back. (A write during the check changes
    the head between the two reads, so the comparison is skipped instead.) A
    head whose link is missing or has a different hash means the newest events
    were removed or changed without going through an erasure.
    """
    if stored_head is None or stored_head[0] is None:
        return  # Not recorded yet: no write since the upgrade that added it.
    seq, expected = stored_head
    if last_link is not None and last_link["sequence_no"] == seq and last_link["chain_hash"] == expected:
        return

    found = await _link_hash_at(pool, tenant_id, agent_id, seq)
    if found == expected and last_link is not None and seq < last_link["sequence_no"]:
        summary["stale_head"] = 1
        return

    summary["breaks"] += 1
    problems.append({
        "sequence_no": seq,
        "problem": "head_mismatch",
        "detail": "The agent's recorded newest link is missing or different: the newest "
                  "events were removed or changed without going through an erasure.",
    })
