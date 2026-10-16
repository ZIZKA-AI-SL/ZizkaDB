"""Deleting an event that has a causal child must not fail.

`events.parent_event_id` used to reference `events(event_id)` with no ON DELETE
action, so GDPR forget and agent delete raised a foreign-key violation as soon
as any deleted event was the parent of another event — the normal case for
cross-agent causal chains.

These tests run the real route handlers against a real PostgreSQL through the
shared ``pg_db`` fixture (``core/tests/conftest.py``). They run in CI's
``python-postgres`` job and locally with ZIZKADB_TEST_DATABASE_URL set (see
CONTRIBUTING.md).
"""

import uuid
from unittest.mock import AsyncMock

import asyncpg
import pytest

pytestmark = pytest.mark.postgres

# The events FK exactly as shipped before this fix (no ON DELETE action).
LEGACY_EVENTS_FK = """
    ALTER TABLE events DROP CONSTRAINT IF EXISTS events_parent_event_id_fkey;
    ALTER TABLE events ADD CONSTRAINT events_parent_event_id_fkey
        FOREIGN KEY (parent_event_id) REFERENCES events(event_id);
"""

TENANT = "11111111-1111-1111-1111-111111111111"


async def _seed_cross_agent_chain(pool):
    """planner decides (carries user_id) -> executor acts on that decision."""
    parent = uuid.uuid4()
    child = uuid.uuid4()
    await pool.execute("INSERT INTO tenants (tenant_id, name) VALUES ($1, 't')", TENANT)
    await pool.execute(
        "INSERT INTO agents (agent_id, tenant_id) VALUES ('planner', $1), ('executor', $1)",
        TENANT,
    )
    await pool.execute(
        """INSERT INTO events (event_id, tenant_id, agent_id, event_type, data)
           VALUES ($1, $2, 'planner', 'decision', '{"user_id": "u_123"}')""",
        parent,
        TENANT,
    )
    await pool.execute(
        """INSERT INTO events (event_id, tenant_id, agent_id, event_type, data, parent_event_id)
           VALUES ($1, $2, 'executor', 'action', '{"step": 1}', $3)""",
        child,
        TENANT,
        parent,
    )
    return parent, child


async def _apply_fk_migration(pool):
    from db.event_constraints import EVENTS_PARENT_FK_SET_NULL_SQL

    await pool.execute(EVENTS_PARENT_FK_SET_NULL_SQL)


async def _parent_fks(pool):
    return await pool.fetch(
        """SELECT confdeltype::text AS confdeltype FROM pg_constraint
           WHERE conrelid = 'events'::regclass AND confrelid = 'events'::regclass"""
    )


@pytest.fixture
def no_vectors(monkeypatch):
    monkeypatch.setattr("api.memory._delete_vectors", AsyncMock(return_value=True))
    monkeypatch.setattr("api.memory.embeddings_enabled", lambda: False)
    monkeypatch.setattr("api.agents._purge_agent_vectors", AsyncMock())


async def test_forget_deletes_event_that_has_a_causal_child(pg_db, no_vectors):
    from api.memory import ForgetRequest, forget

    parent, child = await _seed_cross_agent_chain(pg_db)

    result = await forget(
        ForgetRequest(filter_key="user_id", filter_value="u_123"),
        tenant={"tenant_id": TENANT, "agent_id": None},
    )

    assert result["deleted_events"] == 1
    assert await pg_db.fetchval("SELECT count(*) FROM events WHERE event_id = $1", parent) == 0
    # The child's own record survives; only its link to the erased parent goes.
    assert (
        await pg_db.fetchval("SELECT parent_event_id FROM events WHERE event_id = $1", child) is None
    )


async def test_delete_agent_whose_event_is_parent_of_another_agents_event(pg_db, no_vectors):
    from api.agents import delete_agent

    parent, child = await _seed_cross_agent_chain(pg_db)

    result = await delete_agent("planner", tenant={"tenant_id": TENANT})

    assert result["deleted"] is True
    assert await pg_db.fetchval("SELECT count(*) FROM events WHERE agent_id = 'planner'") == 0
    assert await pg_db.fetchval("SELECT count(*) FROM events WHERE event_id = $1", child) == 1


async def test_migration_repairs_a_legacy_install_and_is_idempotent(pg_db, no_vectors):
    from api.memory import ForgetRequest, forget

    await pg_db.execute(LEGACY_EVENTS_FK)
    parent, child = await _seed_cross_agent_chain(pg_db)

    # Before the migration the legacy constraint blocks the delete.
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await forget(
            ForgetRequest(filter_key="user_id", filter_value="u_123"),
            tenant={"tenant_id": TENANT, "agent_id": None},
        )

    await _apply_fk_migration(pg_db)
    await _apply_fk_migration(pg_db)  # second run must be a no-op

    fks = await _parent_fks(pg_db)
    assert [r["confdeltype"] for r in fks] == ["n"]  # exactly one FK, ON DELETE SET NULL

    result = await forget(
        ForgetRequest(filter_key="user_id", filter_value="u_123"),
        tenant={"tenant_id": TENANT, "agent_id": None},
    )
    assert result["deleted_events"] == 1
    assert (
        await pg_db.fetchval("SELECT parent_event_id FROM events WHERE event_id = $1", child) is None
    )
