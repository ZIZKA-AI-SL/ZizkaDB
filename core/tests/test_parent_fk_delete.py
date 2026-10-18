"""Deleting an event that has a causal child must not fail, or alter the child.

`events.parent_event_id` used to reference `events(event_id)` with no ON DELETE
action, so GDPR forget and agent delete raised a foreign-key violation as soon
as any deleted event was the parent of another event — the normal case for
cross-agent causal chains.

The link is no longer a foreign key (see core/db/event_constraints.py). Erasing
a parent leaves the child row exactly as it was, so the child's v2 checksum,
which covers parent_event_id, keeps verifying.

These tests run the real route handlers against a real PostgreSQL through the
shared ``pg_db`` fixture (``core/tests/conftest.py``). They run in CI's
``python-postgres`` job and locally with ZIZKADB_TEST_DATABASE_URL set.
"""

import asyncio
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


@pytest.fixture
def managed(monkeypatch):
    # Two agents in one tenant: the self-hosted one-agent cap must not apply.
    monkeypatch.setenv("DEPLOYMENT_MODE", "managed")


@pytest.fixture
def no_vectors(monkeypatch):
    monkeypatch.setattr("api.memory._delete_vectors", AsyncMock(return_value=True))
    monkeypatch.setattr("api.memory.embeddings_enabled", lambda: False)
    monkeypatch.setattr("api.agents._purge_agent_vectors", AsyncMock())


async def _seed_cross_agent_chain(pool):
    """planner decides (carries user_id) -> executor acts on that decision."""
    from services.event_write import write_event

    tenant = str(await pool.fetchval("INSERT INTO tenants (name) VALUES ('t') RETURNING tenant_id"))
    parent = await write_event(
        tenant_id=tenant, agent="planner", event="decision", data={"user_id": "u_123"}
    )
    child = await write_event(
        tenant_id=tenant,
        agent="executor",
        event="action",
        data={"step": 1},
        parent_id=parent["event_id"],
    )
    return tenant, parent["event_id"], child["event_id"]


async def _child_state(pool, child_id):
    """(parent_event_id, checksum status) of the child, verified like the API does."""
    from services.checksum import CHECKSUM_COLUMNS, verify_stored_checksum

    row = await pool.fetchrow(
        f"SELECT {CHECKSUM_COLUMNS} FROM events WHERE event_id = $1::uuid", child_id
    )
    return str(row["parent_event_id"]), verify_stored_checksum(row, None)["status"]


async def _apply_migration(pool):
    from db.event_constraints import EVENTS_PARENT_FK_DROP_SQL

    await pool.execute(EVENTS_PARENT_FK_DROP_SQL)


async def _remaining_parent_fks(pool):
    from db.event_constraints import EVENTS_PARENT_FK_COUNT_SQL

    return await pool.fetchval(EVENTS_PARENT_FK_COUNT_SQL)


async def _parent_fk_names(pool):
    rows = await pool.fetch(
        """SELECT conname FROM pg_constraint
           WHERE conrelid = 'events'::regclass AND confrelid = 'events'::regclass"""
    )
    return sorted(r["conname"] for r in rows)


async def _forget_u123(tenant):
    from api.memory import ForgetRequest, forget

    return await forget(
        ForgetRequest(filter_key="user_id", filter_value="u_123"),
        tenant={"tenant_id": tenant, "agent_id": None},
    )


async def test_forget_deletes_parent_and_leaves_the_child_untouched(pg_db, managed, no_vectors):
    tenant, parent, child = await _seed_cross_agent_chain(pg_db)
    assert await _child_state(pg_db, child) == (parent, "valid")

    result = await _forget_u123(tenant)

    assert result["deleted_events"] == 1
    assert await pg_db.fetchval("SELECT count(*) FROM events WHERE event_id = $1::uuid", parent) == 0
    # The child row is not modified: same link, checksum still verifies.
    assert await _child_state(pg_db, child) == (parent, "valid")


async def test_delete_agent_leaves_other_agents_children_untouched(pg_db, managed, no_vectors):
    from api.agents import delete_agent

    tenant, parent, child = await _seed_cross_agent_chain(pg_db)

    result = await delete_agent("planner", tenant={"tenant_id": tenant})

    assert result["deleted"] is True
    assert await pg_db.fetchval(
        "SELECT count(*) FROM events WHERE tenant_id = $1::uuid AND agent_id = 'planner'", tenant
    ) == 0
    assert await _child_state(pg_db, child) == (parent, "valid")


async def test_why_ends_where_the_parent_was_erased(pg_db, managed, no_vectors):
    import httpx

    from api.deps import get_tenant
    from main import app

    tenant, parent, child = await _seed_cross_agent_chain(pg_db)
    await _forget_u123(tenant)

    app.dependency_overrides[get_tenant] = lambda: {"tenant_id": tenant, "user_id": None}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/v1/events/{child}/why")
    finally:
        app.dependency_overrides.pop(get_tenant, None)

    assert response.status_code == 200
    assert [e["event_id"] for e in response.json()["chain"]] == [child]


async def test_migration_repairs_a_legacy_install_and_is_idempotent(pg_db, managed, no_vectors):
    await pg_db.execute(LEGACY_EVENTS_FK)
    tenant, parent, child = await _seed_cross_agent_chain(pg_db)

    # Before the migration the legacy constraint blocks the delete.
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await _forget_u123(tenant)

    await _apply_migration(pg_db)
    await _apply_migration(pg_db)  # second run must be a no-op

    assert await _parent_fk_names(pg_db) == []
    assert (await _forget_u123(tenant))["deleted_events"] == 1
    assert await _child_state(pg_db, child) == (parent, "valid")


async def test_concurrent_startups_with_a_differently_named_legacy_fk(pg_db):
    """Every API worker runs the migration at once; none may fail."""
    await pg_db.execute(
        """ALTER TABLE events ADD CONSTRAINT legacy_parent_fk
           FOREIGN KEY (parent_event_id) REFERENCES events(event_id)"""
    )

    # Hold a lock the drop has to wait for, so all four workers start
    # together, as at a real deploy. Without coordination a second worker
    # would try to drop the name the first one had already dropped.
    async with pg_db.acquire() as holder:
        tx = holder.transaction()
        await tx.start()
        await holder.execute("LOCK TABLE events IN ACCESS SHARE MODE")
        workers = [asyncio.create_task(_apply_migration(pg_db)) for _ in range(4)]
        await asyncio.sleep(0.5)
        await tx.rollback()
        await asyncio.gather(*workers)

    assert await _parent_fk_names(pg_db) == []


async def test_every_single_column_parent_fk_is_dropped(pg_db):
    await pg_db.execute(LEGACY_EVENTS_FK)
    await pg_db.execute(
        """ALTER TABLE events ADD CONSTRAINT extra_parent_fk
           FOREIGN KEY (parent_event_id) REFERENCES events(event_id) ON DELETE SET NULL"""
    )

    await _apply_migration(pg_db)

    assert await _parent_fk_names(pg_db) == []


async def test_busy_table_skips_the_migration_instead_of_stalling(pg_db):
    """A long transaction on events must not block startup: skip, keep the old FK."""
    await pg_db.execute(LEGACY_EVENTS_FK)

    async with pg_db.acquire() as holder:
        tx = holder.transaction()
        await tx.start()
        try:
            await holder.execute("LOCK TABLE events IN ACCESS SHARE MODE")
            await _apply_migration(pg_db)  # gives up after lock_timeout, then warns
        finally:
            await tx.rollback()

    assert await _parent_fk_names(pg_db) == ["events_parent_event_id_fkey"]
    assert await _remaining_parent_fks(pg_db) == 1  # init_db logs a warning on this
    await _apply_migration(pg_db)  # next start drops it
    assert await _parent_fk_names(pg_db) == []
    assert await _remaining_parent_fks(pg_db) == 0


async def test_composite_fk_including_the_column_is_left_alone(pg_db):
    await pg_db.execute("ALTER TABLE events ADD CONSTRAINT events_tenant_event_uq UNIQUE (tenant_id, event_id)")
    await pg_db.execute(
        """ALTER TABLE events ADD COLUMN IF NOT EXISTS parent_tenant_id UUID;
           ALTER TABLE events ADD CONSTRAINT composite_parent_fk
               FOREIGN KEY (parent_tenant_id, parent_event_id)
               REFERENCES events(tenant_id, event_id)"""
    )

    await _apply_migration(pg_db)

    assert await _parent_fk_names(pg_db) == ["composite_parent_fk"]
    assert await _remaining_parent_fks(pg_db) == 0  # composite keys do not count
