"""Erasure receipts (ADR-009 H3) on a real Postgres: written, chained, and safe to downgrade."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from api.memory import ForgetRequest, forget
from services.audit_chain import RECEIPT_REASON, erasure_digest, verify_chain
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
    assert [p["problem"] for p in result["problems"]] == ["chain_break"]


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
