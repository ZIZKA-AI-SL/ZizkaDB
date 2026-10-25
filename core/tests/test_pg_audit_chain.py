"""Per-agent hash chain: detects removed, inserted, reordered and edited events."""

import asyncio
import json

import pytest

from api.memory import ForgetRequest, forget
from services.audit_chain import verify_chain
from services.event_write import write_event

pytestmark = pytest.mark.postgres

AGENT = "bot"


async def _setup(pool, n=5) -> tuple[str, list[dict]]:
    tenant_id = str(await pool.fetchval(
        "INSERT INTO tenants (name) VALUES ('acme') RETURNING tenant_id"
    ))
    written = [
        await write_event(tenant_id=tenant_id, agent=AGENT, event="step",
                          data={"i": i, "user": f"u{i}"})
        for i in range(n)
    ]
    return tenant_id, written


async def test_fresh_chain_is_intact_and_head_is_last_event(pg_db):
    tenant_id, written = await _setup(pg_db)
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True
    assert result["summary"]["chained"] == 5
    assert result["chain_head"] == {
        "sequence_no": written[-1]["sequence_no"],
        "chain_hash": written[-1]["chain_hash"],
    }


async def test_edited_row_is_detected(pg_db):
    tenant_id, written = await _setup(pg_db)
    await pg_db.execute(
        "UPDATE events SET data = '{\"i\": 99}' WHERE event_id = $1", written[2]["event_id"]
    )
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is False
    assert result["problems"][0]["problem"] == "checksum_mismatch"


async def test_silently_deleted_row_breaks_the_chain(pg_db):
    tenant_id, written = await _setup(pg_db)
    await pg_db.execute("DELETE FROM events WHERE event_id = $1", written[2]["event_id"])
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is False
    assert result["problems"][0]["problem"] == "chain_break"
    assert result["problems"][0]["sequence_no"] == written[3]["sequence_no"]


async def test_recomputed_checksum_still_breaks_the_chain(pg_db):
    """Swapping a row's content and checksum together is caught by the next link."""
    tenant_id, written = await _setup(pg_db)
    await pg_db.execute(
        "UPDATE events SET chain_hash = repeat('0', 64) WHERE event_id = $1",
        written[1]["event_id"],
    )
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert {p["sequence_no"] for p in result["problems"]} == {
        written[1]["sequence_no"], written[2]["sequence_no"]
    }


async def test_forget_leaves_tombstones_and_the_chain_verifies(pg_db):
    tenant_id, written = await _setup(pg_db)
    await forget(ForgetRequest(filter_key="user", filter_value="u2"),
                 tenant={"tenant_id": tenant_id, "agent_id": AGENT})
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True
    assert result["summary"]["tombstones"] == 1
    assert result["summary"]["events"] == 4
    row = await pg_db.fetchrow("SELECT * FROM event_tombstones")
    assert row["reason"] == "forget" and row["chain_hash"] == written[2]["chain_hash"]

    # Writing after a forget of the newest event continues from its tombstone.
    await forget(ForgetRequest(filter_key="user", filter_value="u4"),
                 tenant={"tenant_id": tenant_id, "agent_id": AGENT})
    await write_event(tenant_id=tenant_id, agent=AGENT, event="step", data={"i": 5})
    assert (await verify_chain(pg_db, tenant_id, AGENT))["intact"] is True


async def test_legacy_rows_before_the_chain_are_allowed(pg_db):
    tenant_id = str(await pg_db.fetchval(
        "INSERT INTO tenants (name) VALUES ('acme') RETURNING tenant_id"
    ))
    await pg_db.execute("INSERT INTO agents (agent_id, tenant_id) VALUES ($1, $2)", AGENT, tenant_id)
    await pg_db.execute(
        "INSERT INTO events (tenant_id, agent_id, event_type, data, checksum) VALUES ($1, $2, 'old', $3, 'x')",
        tenant_id, AGENT, json.dumps({}),
    )
    await write_event(tenant_id=tenant_id, agent=AGENT, event="new", data={})
    result = await verify_chain(pg_db, tenant_id, AGENT, legacy_until_seq=10**9)
    assert result["summary"]["unchained_legacy"] == 1
    assert result["summary"]["breaks"] == 0


async def test_row_inserted_behind_the_write_path_is_detected(pg_db):
    tenant_id, _ = await _setup(pg_db, n=2)
    await pg_db.execute(
        "INSERT INTO events (tenant_id, agent_id, event_type, data, checksum) VALUES ($1, $2, 'sneaky', '{}', 'x')",
        tenant_id, AGENT,
    )
    problems = (await verify_chain(pg_db, tenant_id, AGENT))["problems"]
    assert "unchained_after_chain_start" in {p["problem"] for p in problems}


async def test_resume_from_an_anchor(pg_db):
    tenant_id, written = await _setup(pg_db, n=3)
    head = (await verify_chain(pg_db, tenant_id, AGENT))["chain_head"]
    await write_event(tenant_id=tenant_id, agent=AGENT, event="later", data={})

    ok = await verify_chain(pg_db, tenant_id, AGENT,
                            after_seq=head["sequence_no"], anchor=head["chain_hash"])
    assert ok["intact"] is True and ok["summary"]["events"] == 1

    bad = await verify_chain(pg_db, tenant_id, AGENT,
                             after_seq=head["sequence_no"], anchor="f" * 64)
    assert bad["intact"] is False
    assert bad["problems"][0]["problem"] == "anchor_mismatch"


async def test_concurrent_writes_keep_one_linear_chain(pg_db):
    tenant_id, _ = await _setup(pg_db, n=1)
    await asyncio.gather(*[
        write_event(tenant_id=tenant_id, agent=AGENT, event="burst", data={"n": n})
        for n in range(20)
    ])
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True and result["summary"]["chained"] == 21
