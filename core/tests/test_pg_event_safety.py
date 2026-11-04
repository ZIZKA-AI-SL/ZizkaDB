"""Idempotent event writes and the migration lock, against a real Postgres."""

import asyncio

import pytest

import db.connection as connection
from services.event_write import write_event

pytestmark = pytest.mark.postgres

AGENT = "bot"


async def _tenant(pool) -> str:
    return str(await pool.fetchval(
        "INSERT INTO tenants (name) VALUES ('acme') RETURNING tenant_id"
    ))


async def test_same_idempotency_key_returns_the_first_event(pg_db):
    tenant_id = await _tenant(pg_db)
    kwargs = dict(tenant_id=tenant_id, agent=AGENT, event="tool_call",
                  data={"tool": "refund"}, idempotency_key="req-1")

    first = await write_event(**kwargs)
    again = await write_event(**kwargs)

    assert first["duplicate"] is False
    assert again["duplicate"] is True
    assert again["event_id"] == first["event_id"]
    assert again["checksum"] == first["checksum"]
    assert await pg_db.fetchval("SELECT COUNT(*) FROM events") == 1
    assert await pg_db.fetchval("SELECT event_count FROM agents") == 1


async def test_concurrent_retries_store_one_event(pg_db):
    tenant_id = await _tenant(pg_db)
    await write_event(tenant_id=tenant_id, agent=AGENT, event="warmup", data={})

    results = await asyncio.gather(*[
        write_event(tenant_id=tenant_id, agent=AGENT, event="tool_call",
                    data={"n": 1}, idempotency_key="race")
        for _ in range(5)
    ])

    assert len({r["event_id"] for r in results}) == 1
    assert sum(not r["duplicate"] for r in results) == 1
    assert await pg_db.fetchval(
        "SELECT COUNT(*) FROM events WHERE idempotency_key = 'race'"
    ) == 1
    assert await pg_db.fetchval("SELECT event_count FROM agents") == 2


async def test_without_a_key_every_write_is_stored(pg_db):
    tenant_id = await _tenant(pg_db)
    for _ in range(2):
        await write_event(tenant_id=tenant_id, agent=AGENT, event="e", data={})
    assert await pg_db.fetchval("SELECT COUNT(*) FROM events") == 2


async def test_migrations_from_several_workers_at_once(pg_db):
    """init_db's DDL runs under an advisory lock; parallel runs must not fail."""
    await asyncio.gather(*[connection._run_migrations() for _ in range(4)])
    async with pg_db.acquire() as a, pg_db.acquire() as b:
        await a.execute("SELECT pg_advisory_lock($1)", connection.MIGRATION_LOCK_ID)
        try:
            assert not await b.fetchval(
                "SELECT pg_try_advisory_lock($1)", connection.MIGRATION_LOCK_ID
            )
        finally:
            await a.execute("SELECT pg_advisory_unlock($1)", connection.MIGRATION_LOCK_ID)
