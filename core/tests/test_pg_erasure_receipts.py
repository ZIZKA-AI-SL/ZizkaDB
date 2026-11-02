"""Erasure receipts (ADR-009 H3) on a real Postgres: written, chained, checked, and safe to downgrade."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from api.memory import ForgetRequest, forget
from services import audit_chain
from services.audit_chain import RECEIPT_REASON, compute_chain_hash, erasure_digest, verify_chain
from services.event_write import write_event

pytestmark = pytest.mark.postgres

AGENT = "bot"


async def _tenant(pool) -> str:
    return str(await pool.fetchval("INSERT INTO tenants (name) VALUES ('acme') RETURNING tenant_id"))


async def _write(tenant_id: str, user: str, agent: str = AGENT, **kw) -> dict:
    return await write_event(tenant_id=tenant_id, agent=agent, event="step", data={"user": user}, **kw)


async def _forget(tenant_id: str, value: str, agent: str | None = AGENT) -> dict:
    return await forget(ForgetRequest(filter_key="user", filter_value=value),
                        tenant={"tenant_id": tenant_id, "agent_id": agent})


async def _receipts(pool, tenant_id: str, agent: str = AGENT) -> list:
    return await pool.fetch(
        """
        SELECT event_id, sequence_no, chain_hash, checksum, erased_count FROM event_tombstones
        WHERE tenant_id = $1 AND agent_id = $2 AND reason = $3 ORDER BY sequence_no
        """,
        tenant_id, agent, RECEIPT_REASON,
    )


async def _sealed_links(pool, tenant_id: str, receipt_id) -> list[tuple[int, str]]:
    rows = await pool.fetch(
        """
        SELECT sequence_no, chain_hash FROM event_tombstones
        WHERE tenant_id = $1 AND receipt_id = $2 ORDER BY sequence_no
        """,
        tenant_id, receipt_id,
    )
    return [(r["sequence_no"], r["chain_hash"]) for r in rows]


async def test_a_forget_seals_its_tombstones_with_a_chained_receipt(pg_db):
    tenant_id = await _tenant(pg_db)
    for user in ("gone", "kept", "gone", "kept"):
        await _write(tenant_id, user)

    assert (await _forget(tenant_id, "gone"))["deleted_events"] == 2

    [receipt] = await _receipts(pg_db, tenant_id)
    links = await _sealed_links(pg_db, tenant_id, receipt["event_id"])
    assert receipt["erased_count"] == len(links) == 2
    assert receipt["checksum"] == erasure_digest(links, "forget")
    assert receipt["sequence_no"] > max(seq for seq, _ in links)
    head = await pg_db.fetchrow(
        "SELECT chain_head_seq, chain_head_hash FROM agents WHERE tenant_id = $1", tenant_id
    )
    assert (head["chain_head_seq"], head["chain_head_hash"]) == (receipt["sequence_no"], receipt["chain_hash"])

    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True, result["problems"]
    assert (result["summary"]["tombstones"], result["summary"]["receipts"]) == (2, 1)
    assert result["chain_head"]["sequence_no"] == receipt["sequence_no"]


async def test_a_forget_across_two_agents_writes_one_receipt_each(pg_db, monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "managed")  # several agents are Cloud only

    async def vectors_deleted(event_ids, tenant):
        return True

    monkeypatch.setattr("api.memory._delete_vectors", vectors_deleted)
    tenant_id = await _tenant(pg_db)
    for agent in ("alpha", "beta"):
        await _write(tenant_id, "gone", agent=agent)
        await _write(tenant_id, "gone", agent=agent)
        await _write(tenant_id, "kept", agent=agent)

    assert (await _forget(tenant_id, "gone", agent=None))["deleted_events"] == 4

    for agent in ("alpha", "beta"):
        [receipt] = await _receipts(pg_db, tenant_id, agent)
        assert receipt["erased_count"] == 2
        links = await _sealed_links(pg_db, tenant_id, receipt["event_id"])
        assert receipt["checksum"] == erasure_digest(links, "forget")
        assert (await verify_chain(pg_db, tenant_id, agent))["intact"] is True


async def test_erasing_only_rows_from_before_the_chain_writes_no_receipt(pg_db):
    tenant_id = await _tenant(pg_db)
    for _ in range(3):
        await _write(tenant_id, "gone")
    await pg_db.execute("UPDATE events SET chain_hash = NULL WHERE tenant_id = $1", tenant_id)

    assert (await _forget(tenant_id, "gone"))["deleted_events"] == 3
    assert await pg_db.fetchval("SELECT COUNT(*) FROM event_tombstones") == 0


async def test_erasing_rows_from_before_and_after_the_chain_seals_only_the_chained(pg_db):
    """An upgraded agent's old rows have no link: they get no tombstone, the rest do."""
    tenant_id = await _tenant(pg_db)
    legacy = [await _write(tenant_id, "gone") for _ in range(2)]
    await pg_db.execute(
        "UPDATE events SET chain_hash = NULL WHERE event_id = ANY($1::uuid[])",
        [w["event_id"] for w in legacy],
    )
    # Written before the chain and the recorded head existed.
    await pg_db.execute("UPDATE agents SET chain_head_seq = NULL, chain_head_hash = NULL")
    await _write(tenant_id, "gone")
    await _write(tenant_id, "kept")

    assert (await _forget(tenant_id, "gone"))["deleted_events"] == 3
    [receipt] = await _receipts(pg_db, tenant_id)
    assert receipt["erased_count"] == 1
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True, result["problems"]
    assert (result["summary"]["tombstones"], result["summary"]["receipts"]) == (1, 1)


async def test_a_receipt_with_its_checksum_blanked_is_not_legacy(pg_db):
    """Receipts are always newer than the checksum cutoff, so they need one."""
    tenant_id = await _tenant(pg_db)
    await _write(tenant_id, "gone")
    await _forget(tenant_id, "gone")
    await pg_db.execute(
        "UPDATE event_tombstones SET checksum = NULL, deleted_at = '2000-01-01' WHERE reason = $1",
        RECEIPT_REASON,
    )

    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is False
    assert result["summary"]["legacy_tombstones"] == 0
    # Its link breaks, and it no longer matches the tombstone it seals.
    assert [p["problem"] for p in result["problems"]] == ["chain_break", "receipt_digest_mismatch"]


async def test_the_next_write_links_to_the_receipt(pg_db):
    tenant_id = await _tenant(pg_db)
    await _write(tenant_id, "gone")
    await _forget(tenant_id, "gone")
    written = await _write(tenant_id, "later")

    [receipt] = await _receipts(pg_db, tenant_id)
    assert written["sequence_no"] > receipt["sequence_no"]
    assert await pg_db.fetchval("SELECT COUNT(*) FROM chain_head_breaks") == 0
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True, result["problems"]
    assert result["summary"]["stale_head"] == 0


async def test_an_older_image_erasing_after_a_receipt_still_verifies(pg_db):
    """0.5.0 reads a receipt as a tombstone: it erases and writes after it."""
    tenant_id = await _tenant(pg_db)
    await _write(tenant_id, "gone")
    old = await _write(tenant_id, "old-image")
    await _forget(tenant_id, "gone")
    await pg_db.execute(
        """
        INSERT INTO event_tombstones (tenant_id, agent_id, event_id, sequence_no, chain_hash, reason)
        SELECT tenant_id, agent_id, event_id, sequence_no, chain_hash, 'forget'
        FROM events WHERE event_id = $1
        """,
        old["event_id"],
    )
    await pg_db.execute("DELETE FROM events WHERE event_id = $1", old["event_id"])
    await _write(tenant_id, "after")

    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True, result["problems"]
    assert (result["summary"]["tombstones"], result["summary"]["receipts"]) == (2, 1)


async def test_retention_writes_one_receipt_per_batch(pg_db, monkeypatch):
    from services import retention

    tenant_id = await _tenant(pg_db)
    old = datetime.now(timezone.utc) - timedelta(days=40)
    for i in range(3):
        await _write(tenant_id, f"u{i}", occurred_at=old)
    await _write(tenant_id, "recent")
    monkeypatch.setenv("EVENT_RETENTION_DAYS", "30")

    assert await retention.purge_expired(pg_db, batch=2) == 3

    receipts = await _receipts(pg_db, tenant_id)
    assert [r["erased_count"] for r in receipts] == [2, 1]
    for r in receipts:
        assert r["checksum"] == erasure_digest(await _sealed_links(pg_db, tenant_id, r["event_id"]), "retention")
    assert (await verify_chain(pg_db, tenant_id, AGENT))["intact"] is True


async def test_forgets_racing_writes_keep_one_verifiable_chain(pg_db):
    """The receipt adds the agent row lock to forget: writers wait, nothing deadlocks."""
    tenant_id = await _tenant(pg_db)
    for round_no in range(10):
        for i in range(5):
            await _write(tenant_id, f"gone{round_no}")
        writes = [_write(tenant_id, f"keep{round_no}-{i}") for i in range(10)]
        results = await asyncio.wait_for(
            asyncio.gather(_forget(tenant_id, f"gone{round_no}"), *writes), timeout=30
        )
        assert results[0]["deleted_events"] == 5

    assert len(await _receipts(pg_db, tenant_id)) == 10
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True, result["problems"]



# --------------------------------------------------------------------
# The verifier checks receipts
# --------------------------------------------------------------------

async def _rechain(pool, tenant_id: str, agent: str = AGENT) -> None:
    """What a database writer does to hide a change: recompute every link in
    sequence order, events and tombstones alike, and the recorded head."""
    rows = await pool.fetch(
        """
        SELECT 'events' AS tbl, event_id, sequence_no, checksum FROM events
        WHERE tenant_id = $1 AND agent_id = $2 AND chain_hash IS NOT NULL
        UNION ALL
        SELECT 'event_tombstones', event_id, sequence_no, checksum FROM event_tombstones
        WHERE tenant_id = $1 AND agent_id = $2
        ORDER BY sequence_no
        """,
        tenant_id, agent,
    )
    prev = None
    for r in rows:
        prev = compute_chain_hash(prev, r["checksum"] or "")
        await pool.execute(
            f"UPDATE {r['tbl']} SET chain_hash = $1 WHERE tenant_id = $2 AND event_id = $3",
            prev, tenant_id, r["event_id"],
        )
    await pool.execute(
        "UPDATE agents SET chain_head_seq = $1, chain_head_hash = $2 WHERE tenant_id = $3 AND agent_id = $4",
        rows[-1]["sequence_no"], prev, tenant_id, agent,
    )


async def _older_image_erases(pool, event_id) -> None:
    """0.5.0's forget: a tombstone without a receipt, then the delete."""
    await pool.execute(
        """
        INSERT INTO event_tombstones (tenant_id, agent_id, event_id, sequence_no, chain_hash, reason)
        SELECT tenant_id, agent_id, event_id, sequence_no, chain_hash, 'forget'
        FROM events WHERE event_id = $1
        """,
        event_id,
    )
    await pool.execute("DELETE FROM events WHERE event_id = $1", event_id)


def _problems(result: dict) -> list[str]:
    return [p["problem"] for p in result["problems"]]


async def test_a_sealed_tombstone_removed_with_the_chain_recomputed_is_caught(pg_db):
    """Every link and the head are recomputed: before receipts were checked,
    this passed as intact. The receipt still says it sealed two."""
    tenant_id = await _tenant(pg_db)
    for user in ("gone", "kept", "gone", "kept"):
        await _write(tenant_id, user)
    await _forget(tenant_id, "gone")
    first = await pg_db.fetchval(
        "SELECT event_id FROM event_tombstones WHERE reason = 'forget' ORDER BY sequence_no LIMIT 1"
    )
    await pg_db.execute("DELETE FROM event_tombstones WHERE event_id = $1", first)
    await _rechain(pg_db, tenant_id)

    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is False
    assert _problems(result) == ["receipt_digest_mismatch"]
    assert "seals 2 tombstone(s), but 1 name it" in result["problems"][0]["detail"]


async def test_a_sealed_tombstone_changed_without_touching_its_link_is_caught(pg_db):
    """A tombstone's reason is not part of any link; the receipt's digest covers it."""
    tenant_id = await _tenant(pg_db)
    gone = await _write(tenant_id, "gone")
    await _write(tenant_id, "kept")
    await _forget(tenant_id, "gone")
    await pg_db.execute(
        "UPDATE event_tombstones SET reason = 'retention' WHERE event_id = $1", gone["event_id"]
    )

    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is False
    assert _problems(result) == ["receipt_digest_mismatch"]


async def test_a_tombstone_moved_to_another_erasure_is_caught(pg_db):
    tenant_id = await _tenant(pg_db)
    a = await _write(tenant_id, "a")
    await _write(tenant_id, "b")
    await _forget(tenant_id, "a")
    await _forget(tenant_id, "b")
    later = (await _receipts(pg_db, tenant_id))[1]["event_id"]
    await pg_db.execute(
        "UPDATE event_tombstones SET receipt_id = $1 WHERE event_id = $2", later, a["event_id"]
    )

    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert _problems(result) == ["receipt_digest_mismatch", "receipt_digest_mismatch"]


async def test_a_removed_receipt_is_caught_even_with_the_chain_recomputed(pg_db):
    tenant_id = await _tenant(pg_db)
    for user in ("gone", "gone", "kept"):
        await _write(tenant_id, user)
    await _forget(tenant_id, "gone")
    [receipt] = await _receipts(pg_db, tenant_id)
    sealed = await _sealed_links(pg_db, tenant_id, receipt["event_id"])
    await pg_db.execute("DELETE FROM event_tombstones WHERE reason = $1", RECEIPT_REASON)
    await _rechain(pg_db, tenant_id)

    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is False
    # One problem for the missing receipt, not one per tombstone that names it.
    assert _problems(result) == ["receipt_missing"]
    assert result["summary"]["breaks"] == 1
    [problem] = result["problems"]
    assert problem["receipt_id"] == str(receipt["event_id"])
    assert problem["tombstones"] == 2
    assert (problem["sequence_no"], problem["last_sequence_no"]) == (sealed[0][0], sealed[-1][0])


async def test_an_older_image_erasing_after_the_upgrade_is_counted_not_a_break(pg_db):
    """Without a key, 0.5.0's erasure during a rolling deploy and a tombstone
    inserted by hand look the same: the chain stays intact, and the summary
    tells the auditor to look."""
    tenant_id = await _tenant(pg_db)
    old = await _write(tenant_id, "old-image")
    await _write(tenant_id, "kept")
    await _older_image_erases(pg_db, old["event_id"])

    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True, result["problems"]
    assert result["summary"]["unsealed_tombstones"] == 1

    # The same tombstone from before receipts were checked is normal.
    await pg_db.execute("UPDATE event_tombstones SET deleted_at = '2000-01-01'")
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True and result["summary"]["unsealed_tombstones"] == 0


async def test_resuming_from_an_anchor_checks_tombstones_before_it(pg_db):
    """The receipt is after the anchor, the tombstone it seals before it."""
    tenant_id = await _tenant(pg_db)
    gone = await _write(tenant_id, "gone")
    anchor = await _write(tenant_id, "kept")
    await _forget(tenant_id, "gone")
    kw = {"after_seq": anchor["sequence_no"], "anchor": anchor["chain_hash"]}

    resumed = await verify_chain(pg_db, tenant_id, AGENT, **kw)
    assert resumed["intact"] is True, resumed["problems"]
    assert (resumed["summary"]["tombstones"], resumed["summary"]["receipts"]) == (0, 1)

    await pg_db.execute(
        "UPDATE event_tombstones SET reason = 'retention' WHERE event_id = $1", gone["event_id"]
    )
    assert _problems(await verify_chain(pg_db, tenant_id, AGENT, **kw)) == ["receipt_digest_mismatch"]


class _PoolThatActsBefore:
    """Runs ``action`` once, just before the first ``fetch`` that ``when`` picks."""

    def __init__(self, pool, action, when):
        self._pool, self._action, self._when = pool, action, when
        self._done, self._fetches = False, 0

    async def fetch(self, query, *args):
        self._fetches += 1
        if not self._done and self._when(query, self._fetches):
            self._done = True
            await self._action()
        return await self._pool.fetch(query, *args)

    def __getattr__(self, name):
        return getattr(self._pool, name)


async def test_an_erasure_during_the_check_is_not_a_mismatch(pg_db, monkeypatch):
    """The walk already passed one of the erased rows as an event: the receipt
    is checked against the tombstones that name it, not the ones walked."""
    monkeypatch.setattr(audit_chain, "CHAIN_PAGE_SIZE", 2)
    tenant_id = await _tenant(pg_db)
    for user in ("gone", "kept", "kept", "kept", "gone", "kept"):
        await _write(tenant_id, user)

    async def erase():
        await _forget(tenant_id, "gone")

    pool = _PoolThatActsBefore(pg_db, erase, lambda query, n: n == 2)   # after the first page
    result = await verify_chain(pool, tenant_id, AGENT)
    assert result["intact"] is True, result["problems"]
    assert result["summary"]["receipts"] == 1


async def test_an_agent_deleted_before_its_receipt_is_checked_is_not_reported(pg_db):
    tenant_id = await _tenant(pg_db)
    await _write(tenant_id, "gone")
    await _write(tenant_id, "kept")
    await _forget(tenant_id, "gone")

    async def delete_agent():
        for table in ("event_tombstones", "events", "agents"):
            await pg_db.execute(f"DELETE FROM {table} WHERE tenant_id = $1", tenant_id)

    pool = _PoolThatActsBefore(pg_db, delete_agent, lambda query, n: "receipt_id = ANY" in query)
    result = await verify_chain(pool, tenant_id, AGENT)
    assert not {"receipt_digest_mismatch", "receipt_missing"} & set(_problems(result))


async def _erasure_split_over_two_pages(tenant_id: str, monkeypatch) -> None:
    """Two pages of two rows: the erased tombstones on the first, the receipt on
    the second, like a retention batch larger than CHAIN_PAGE_SIZE."""
    monkeypatch.setattr(audit_chain, "CHAIN_PAGE_SIZE", 2)
    for user in ("gone", "gone", "kept"):
        await _write(tenant_id, user)
    await _forget(tenant_id, "gone")


async def _delete_agent(pool, tenant_id: str) -> None:
    """What DELETE /v1/agents/{id} does to the tables (without Qdrant)."""
    for table in ("event_tombstones", "events", "agents"):
        await pool.execute(f"DELETE FROM {table} WHERE tenant_id = $1", tenant_id)


async def test_an_agent_deleted_between_the_pages_of_an_erasure_is_not_reported(pg_db, monkeypatch):
    """The tombstones were walked, then the agent went with their receipt
    before the next page: not a missing receipt."""
    tenant_id = await _tenant(pg_db)
    await _erasure_split_over_two_pages(tenant_id, monkeypatch)

    async def delete_agent():
        await _delete_agent(pg_db, tenant_id)

    pool = _PoolThatActsBefore(pg_db, delete_agent, lambda query, n: n == 2)   # after the first page
    result = await verify_chain(pool, tenant_id, AGENT)
    assert result["summary"]["tombstones"] == 2
    assert result["intact"] is True, result["problems"]


async def test_an_agent_re_created_between_the_pages_of_an_erasure_is_not_reported(pg_db, monkeypatch):
    """Deleted and created again under the same name (a new first_seen): the
    old receipt is gone with the old agent, not removed."""
    tenant_id = await _tenant(pg_db)
    await _erasure_split_over_two_pages(tenant_id, monkeypatch)

    async def delete_and_write_again():
        await _delete_agent(pg_db, tenant_id)
        await _write(tenant_id, "new")

    pool = _PoolThatActsBefore(pg_db, delete_and_write_again, lambda query, n: n == 2)
    result = await verify_chain(pool, tenant_id, AGENT)
    assert "receipt_missing" not in _problems(result)


async def test_a_receipt_removed_after_a_split_erasure_is_still_caught(pg_db, monkeypatch):
    """The same split erasure, nothing deleted during the check: the guard
    only skips the report when the agent row changed."""
    tenant_id = await _tenant(pg_db)
    await _erasure_split_over_two_pages(tenant_id, monkeypatch)
    await pg_db.execute("DELETE FROM event_tombstones WHERE reason = $1", RECEIPT_REASON)
    await _rechain(pg_db, tenant_id)

    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert _problems(result) == ["receipt_missing"]
    assert result["problems"][0]["tombstones"] == 2


async def test_upgrade_records_the_receipt_cutoff_once_and_builds_the_index(pg_db):
    """A database from before receipts were checked: its older tombstones stay
    normal, and a later start keeps the first cutoff."""
    import db.connection as connection

    tenant_id = await _tenant(pg_db)
    before = await _write(tenant_id, "before")
    await _write(tenant_id, "kept")
    await pg_db.execute("""
        DROP INDEX idx_event_tombstones_receipt;
        DROP TABLE tombstone_receipt_cutoff;
    """)
    await _older_image_erases(pg_db, before["event_id"])   # erased before the upgrade

    await connection._run_migrations()
    since = await pg_db.fetchval("SELECT since FROM tombstone_receipt_cutoff")
    await connection._run_migrations()

    assert await pg_db.fetchval("SELECT COUNT(*) FROM tombstone_receipt_cutoff") == 1
    assert await pg_db.fetchval("SELECT since FROM tombstone_receipt_cutoff") == since
    assert await pg_db.fetchval(
        """
        SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
        WHERE c.relname = 'idx_event_tombstones_receipt'
        """
    ) is True
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True, result["problems"]
    assert result["summary"]["unsealed_tombstones"] == 0
