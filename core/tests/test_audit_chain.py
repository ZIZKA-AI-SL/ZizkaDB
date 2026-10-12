"""Audit hash chain: tamper detection, lawful erasure, and the real write path.

The unit tests build chains in memory. The PostgreSQL tests at the bottom run
``write_event`` and ``verify_audit_chain`` against a real database and are
skipped unless ZIZKADB_TEST_DATABASE_URL points at a disposable one.
"""

import asyncio
import datetime
import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services import audit_chain

TENANT = "11111111-1111-1111-1111-111111111111"
T0 = datetime.datetime(2026, 10, 1, 12, 0, tzinfo=datetime.timezone.utc)


def _row(seq, agent="planner", data='{"x": 1}', parent=None, meta=None):
    return {
        "event_id": uuid.UUID(int=seq),
        "tenant_id": TENANT,
        "agent_id": agent,
        "timestamp": T0 + datetime.timedelta(seconds=seq),
        "event_type": "decision",
        "data_text": data,
        "metadata_text": meta,
        "parent_event_id": parent,
        "session_id": "s1",
        "sequence_no": seq,
    }


def _chain(rows):
    prev = None
    for r in rows:
        r["prev_chain_hash"] = prev
        r["chain_hash"] = audit_chain.link_hash(prev, r)
        prev = r["chain_hash"]
    return rows


def _tombstone(row):
    return {
        "event_id": row["event_id"],
        "sequence_no": row["sequence_no"],
        "chain_hash": row["chain_hash"],
        "prev_chain_hash": row["prev_chain_hash"],
        "erased": True,
    }


def test_untouched_chain_verifies():
    rows = _chain([_row(i) for i in range(1, 6)])
    result = audit_chain.verify_entries(rows)
    assert result["ok"] is True
    assert result["checked"] == 5
    assert result["head"] == rows[-1]["chain_hash"]


@pytest.mark.parametrize(
    "field, value",
    [
        ("data_text", '{"x": 2}'),
        ("agent_id", "executor"),
        ("timestamp", T0),
        ("metadata_text", '{"approved_by": "nobody"}'),
        ("session_id", "s2"),
        ("parent_event_id", uuid.UUID(int=1)),
    ],
)
def test_editing_any_audited_column_is_detected(field, value):
    rows = _chain([_row(i) for i in range(1, 6)])
    rows[2][field] = value
    result = audit_chain.verify_entries(rows)
    assert result["ok"] is False
    assert result["first_problem"] == {
        "kind": "content_changed",
        "event_id": str(uuid.UUID(int=3)),
        "sequence_no": 3,
    }


def test_deleting_an_event_without_erasure_record_is_detected():
    rows = _chain([_row(i) for i in range(1, 6)])
    del rows[2]
    result = audit_chain.verify_entries(rows)
    assert result["first_problem"]["kind"] == "missing_or_reordered_link"
    assert result["first_problem"]["sequence_no"] == 4


def test_swapping_two_events_is_detected():
    rows = _chain([_row(i) for i in range(1, 6)])
    rows[1]["sequence_no"], rows[2]["sequence_no"] = 3, 2
    assert audit_chain.verify_entries(rows)["ok"] is False


def test_recomputing_one_rows_hash_still_breaks_the_next_link():
    rows = _chain([_row(i) for i in range(1, 6)])
    rows[2]["data_text"] = '{"x": 2}'
    rows[2]["chain_hash"] = audit_chain.link_hash(rows[2]["prev_chain_hash"], rows[2])
    result = audit_chain.verify_entries(rows)
    assert result["first_problem"]["kind"] == "missing_or_reordered_link"
    assert result["first_problem"]["sequence_no"] == 4


def test_with_a_key_a_database_writer_cannot_forge_a_valid_chain(monkeypatch):
    monkeypatch.setenv("AUDIT_CHAIN_KEY", "kept-outside-the-database")
    rows = _chain([_row(i) for i in range(1, 6)])

    # Attacker with DB write access edits row 3 and recomputes every later
    # link, but only knows the unkeyed construction.
    monkeypatch.delenv("AUDIT_CHAIN_KEY")
    rows[2]["data_text"] = '{"x": 2}'
    prev = rows[1]["chain_hash"]
    for r in rows[2:]:
        r["prev_chain_hash"] = prev
        r["chain_hash"] = audit_chain.link_hash(prev, r)
        prev = r["chain_hash"]

    monkeypatch.setenv("AUDIT_CHAIN_KEY", "kept-outside-the-database")
    result = audit_chain.verify_entries(rows)
    assert result["ok"] is False
    assert result["first_problem"]["sequence_no"] == 3


def test_lawful_erasure_keeps_the_chain_valid():
    rows = _chain([_row(i) for i in range(1, 6)])
    entries = rows[:2] + [_tombstone(rows[2])] + rows[3:]
    result = audit_chain.verify_entries(entries)
    assert result["ok"] is True
    assert (result["checked"], result["erased"]) == (4, 1)


def test_child_unlinked_from_an_erased_parent_is_accepted():
    parent_id = uuid.UUID(int=2)
    rows = _chain([_row(1), _row(2), _row(3, agent="executor", parent=parent_id)])
    rows[2]["parent_event_id"] = None  # ON DELETE SET NULL after erasing event 2
    entries = [rows[0], _tombstone(rows[1]), rows[2]]
    assert audit_chain.verify_entries(entries)["ok"] is True


def test_child_unlinked_from_a_parent_that_was_not_erased_is_detected():
    parent_id = uuid.UUID(int=2)
    rows = _chain([_row(1), _row(2), _row(3, agent="executor", parent=parent_id)])
    rows[2]["parent_event_id"] = None
    result = audit_chain.verify_entries(rows)
    assert result["first_problem"]["kind"] == "content_changed"
    assert result["first_problem"]["sequence_no"] == 3


def test_events_written_before_the_chain_are_counted_not_failed():
    legacy = [{**_row(i), "chain_hash": None, "prev_chain_hash": None} for i in (1, 2)]
    rows = _chain([_row(i) for i in (3, 4)])
    result = audit_chain.verify_entries(legacy + rows)
    assert result["ok"] is True
    assert (result["unchained"], result["checked"]) == (2, 2)


# ── real PostgreSQL ──────────────────────────────────────────────────────────

DSN = os.getenv("ZIZKADB_TEST_DATABASE_URL")
pg = pytest.mark.skipif(not DSN, reason="set ZIZKADB_TEST_DATABASE_URL to run")


@pytest.fixture
async def pool(monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    schema = f"chain_test_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(DSN)
    await admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await admin.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    await admin.execute(f"CREATE SCHEMA {schema}")
    await admin.close()
    p = await asyncpg.create_pool(
        DSN, min_size=1, max_size=10, server_settings={"search_path": f"{schema},public"}
    )
    sql = (Path(__file__).resolve().parents[1] / "db" / "schema.sql").read_text()
    async with p.acquire() as conn:
        await conn.execute(sql.replace("CREATE EXTENSION", "-- CREATE EXTENSION"))
        await conn.execute(
            "ALTER TABLE events ADD COLUMN IF NOT EXISTS index_status VARCHAR(16) NOT NULL DEFAULT 'skipped'"
        )
        await conn.execute("INSERT INTO tenants (tenant_id, name) VALUES ($1, 't')", TENANT)
    for mod in ("services.event_write", "api.events", "api.memory"):
        monkeypatch.setattr(f"{mod}.get_pool", lambda: p)
    monkeypatch.setattr("services.event_write.embeddings_enabled", lambda: False)
    try:
        yield p
    finally:
        await p.close()
        admin = await asyncpg.connect(DSN)
        await admin.execute(f"DROP SCHEMA {schema} CASCADE")
        await admin.close()


async def _verify():
    from api.events import verify_audit_chain

    return await verify_audit_chain(session={"tenant_id": TENANT})


@pg
async def test_written_events_verify_and_sql_tampering_is_caught(pool):
    from services.event_write import write_event

    ids = []
    for i in range(5):
        r = await write_event(
            tenant_id=TENANT, agent="planner", event="decision",
            data={"amount": 100 + i, "note": "åäö ✓"}, metadata={"model": "m1"},
        )
        ids.append(r["event_id"])
    result = await _verify()
    assert result["ok"] is True and result["checked"] == 5
    assert result["head"] == r["chain_hash"]

    await pool.execute(
        "UPDATE events SET data = jsonb_set(data, '{amount}', '999') WHERE event_id = $1", ids[2]
    )
    result = await _verify()
    assert result["first_problem"]["kind"] == "content_changed"
    assert result["first_problem"]["event_id"] == ids[2]


@pg
async def test_raw_delete_is_caught_but_gdpr_forget_is_not(pool, monkeypatch):
    from api.memory import ForgetRequest, forget
    from services.event_write import write_event

    ids = [
        (await write_event(tenant_id=TENANT, agent="planner", event="e", data={"user_id": f"u{i}"}))[
            "event_id"
        ]
        for i in range(4)
    ]

    monkeypatch.setattr("api.memory._delete_vectors", AsyncMock(return_value=True))
    monkeypatch.setattr("api.memory.embeddings_enabled", lambda: False)
    await forget(
        ForgetRequest(filter_key="user_id", filter_value="u1"),
        tenant={"tenant_id": TENANT, "agent_id": None},
    )
    result = await _verify()
    assert result["ok"] is True
    assert (result["checked"], result["erased"]) == (3, 1)

    await pool.execute("DELETE FROM events WHERE event_id = $1", ids[2])
    result = await _verify()
    assert result["first_problem"]["kind"] == "missing_or_reordered_link"
    assert result["first_problem"]["event_id"] == ids[3]


@pg
async def test_concurrent_writes_produce_one_valid_chain(pool):
    from services.event_write import write_event

    await asyncio.gather(
        *(
            write_event(tenant_id=TENANT, agent=f"agent-{i % 3}", event="e", data={"i": i})
            for i in range(30)
        )
    )
    result = await _verify()
    assert result["ok"] is True and result["checked"] == 30
