"""Regression tests for the per-agent event counter (agents.event_count).

The agent upsert in services/event_write.py used to create fresh rows at the
schema default (event_count = 0) and only increment on conflict, so every
agent's counter lagged reality by exactly one for its whole lifetime: after
3 successful writes the counter read 2.

These tests need a real Postgres; they are gated behind the ``integration``
marker like the other stack-dependent tests (set DATABASE_URL and
ZIZKADB_RUN_INTEGRATION=1, or pass --run-integration).
"""

import os
import pathlib
import uuid

import asyncpg
import pytest

import db.connection as db_connection
from services.event_write import write_event

pytestmark = pytest.mark.integration

SCHEMA_PATH = pathlib.Path(__file__).resolve().parents[1] / "db" / "schema.sql"


@pytest.fixture()
async def counter_pool(monkeypatch):
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is required for the event counter tests")

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)

    if not await pool.fetchval("SELECT to_regclass('public.tenants') IS NOT NULL"):
        await pool.execute(SCHEMA_PATH.read_text())
    # schema.sql predates the index_status column; init_db() adds it on
    # every boot — mirror that here so write_event's INSERT resolves.
    await pool.execute(
        "ALTER TABLE events ADD COLUMN IF NOT EXISTS index_status "
        "VARCHAR(16) NOT NULL DEFAULT 'skipped'"
    )

    tenant_id = await pool.fetchval(
        "INSERT INTO tenants (name) VALUES ($1) RETURNING tenant_id",
        f"counter-fix-{uuid.uuid4().hex[:12]}",
    )

    # write_event() resolves its pool via db.connection.get_pool().
    monkeypatch.setattr(db_connection, "_pg_pool", pool)

    # Keep the write path fully offline: no embedding provider, no Qdrant.
    monkeypatch.setenv("DEPLOYMENT_MODE", "self_hosted")
    monkeypatch.setenv("EMBEDDINGS_ENABLED", "false")
    monkeypatch.delenv("EMBED_SYNC", raising=False)

    yield pool, str(tenant_id)

    await pool.close()


async def test_three_writes_by_fresh_agent_yield_event_count_three(counter_pool):
    pool, tenant_id = counter_pool
    agent = f"counter-fix-{uuid.uuid4().hex[:12]}"

    for seq in range(3):
        await write_event(
            tenant_id=tenant_id,
            agent=agent,
            event="message",
            data={"seq": seq},
        )

    actual = await pool.fetchval(
        "SELECT COUNT(*) FROM events WHERE agent_id = $1",
        agent,
    )
    counter = await pool.fetchval(
        "SELECT event_count FROM agents WHERE agent_id = $1",
        agent,
    )

    assert actual == 3
    assert counter == 3
