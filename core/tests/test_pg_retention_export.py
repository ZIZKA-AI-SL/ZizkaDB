"""Retention, soft parent links, and export → import, against a real Postgres."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from api.events import ImportRequest, export_events, import_events
from api.memory import ForgetRequest, forget
from services import retention
from services.audit_chain import verify_chain
from services.event_write import write_event

pytestmark = pytest.mark.postgres

AGENT = "bot"
NOW = datetime.now(timezone.utc)


async def _tenant(pool) -> str:
    return str(await pool.fetchval("INSERT INTO tenants (name) VALUES ('acme') RETURNING tenant_id"))


async def _write(tenant_id, days_ago, **kw):
    return await write_event(tenant_id=tenant_id, agent=AGENT, event=kw.pop("event", "e"),
                             data=kw.pop("data", {}), occurred_at=NOW - timedelta(days=days_ago), **kw)


async def test_retention_deletes_only_expired_and_keeps_the_chain(pg_db, monkeypatch):
    tenant_id = await _tenant(pg_db)
    old_parent = await _write(tenant_id, 40)
    await _write(tenant_id, 35)
    await _write(tenant_id, 1, parent_id=old_parent["event_id"])  # child of an expiring event

    monkeypatch.setenv("EVENT_RETENTION_DAYS", "30")
    assert await retention.run_once(pg_db) == 2

    assert await pg_db.fetchval("SELECT COUNT(*) FROM events") == 1
    chain = await verify_chain(pg_db, tenant_id, AGENT)
    assert chain["intact"] is True and chain["summary"]["tombstones"] == 2
    # Retention tombstones keep the checksum, so their links are checked too.
    assert await pg_db.fetchval("SELECT COUNT(*) FROM event_tombstones WHERE checksum IS NULL") == 0
    assert await pg_db.fetchval("SELECT event_count FROM agents") == 1


async def test_agent_setting_overrides_the_default(pg_db, monkeypatch):
    tenant_id = await _tenant(pg_db)
    await _write(tenant_id, 10)
    await _write(tenant_id, 3)
    monkeypatch.delenv("EVENT_RETENTION_DAYS", raising=False)
    assert await retention.run_once(pg_db) == 0  # off by default

    await pg_db.execute("UPDATE agents SET retention_days = 7")
    assert await retention.run_once(pg_db) == 1


async def test_only_one_worker_purges_at_a_time(pg_db):
    async with pg_db.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", retention.RETENTION_LOCK_ID)
        try:
            assert await retention.run_once(pg_db) is None
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", retention.RETENTION_LOCK_ID)


async def test_forget_of_a_parent_with_children_works(pg_db):
    """parent_event_id is a soft link: deleting a parent no longer fails."""
    tenant_id = await _tenant(pg_db)
    parent = await _write(tenant_id, 0, data={"user": "u1"})
    child = await _write(tenant_id, 0, data={"x": 1}, parent_id=parent["event_id"])
    result = await forget(ForgetRequest(filter_key="user", filter_value="u1"),
                          tenant={"tenant_id": tenant_id, "agent_id": AGENT})
    assert result["deleted_events"] == 1
    assert (await verify_chain(pg_db, tenant_id, AGENT))["intact"] is True
    assert await pg_db.fetchval(
        "SELECT parent_event_id::text FROM events WHERE event_id = $1", child["event_id"]
    ) == parent["event_id"]


async def _export(tenant_id) -> list[dict]:
    response = await export_events(agent=AGENT, after_seq=0,
                                   tenant={"tenant_id": tenant_id, "agent_id": AGENT})
    body = b"".join([chunk.encode() if isinstance(chunk, str) else chunk
                     async for chunk in response.body_iterator])
    return [json.loads(line) for line in body.decode().splitlines()]


async def test_export_import_round_trip(pg_db):
    source = await _tenant(pg_db)
    a = await _write(source, 2, event="user_message", data={"text": "hi"})
    await _write(source, 1, event="tool_call", data={"tool": "x"}, parent_id=a["event_id"])
    lines = await _export(source)
    assert [line["event"] for line in lines] == ["user_message", "tool_call"]

    target = await _tenant(pg_db)
    tenant = {"tenant_id": target, "agent_id": AGENT}
    result = await import_events(ImportRequest(events=lines), tenant=tenant)
    assert result["imported"] == 2 and result["failed"] == 0

    rows = await pg_db.fetch(
        "SELECT event_id::text, event_type, parent_event_id::text, timestamp FROM events "
        "WHERE tenant_id = $1 ORDER BY sequence_no", target,
    )
    assert rows[1]["parent_event_id"] == rows[0]["event_id"]
    assert rows[0]["timestamp"].isoformat() == lines[0]["timestamp"]
    assert (await verify_chain(pg_db, target, AGENT))["intact"] is True

    again = await import_events(ImportRequest(events=lines), tenant=tenant)
    assert again["duplicates"] == 2 and again["imported"] == 0


async def test_import_across_requests_remaps_parents(pg_db):
    source = await _tenant(pg_db)
    a = await _write(source, 2)
    await _write(source, 1, parent_id=a["event_id"])
    lines = await _export(source)

    target = await _tenant(pg_db)
    tenant = {"tenant_id": target, "agent_id": AGENT}
    await import_events(ImportRequest(events=lines[:1]), tenant=tenant)
    second = await import_events(ImportRequest(events=lines[1:]), tenant=tenant)
    assert second["parents_dropped"] == 0
