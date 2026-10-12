"""init_db() against a real Postgres: fresh install and restart behaviour."""

import pytest

import db.connection as connection

pytestmark = pytest.mark.postgres


async def _schema_snapshot(pool) -> dict:
    columns = await pool.fetch(
        """
        SELECT table_name, column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, column_name
        """
    )
    indexes = await pool.fetch(
        """
        SELECT tablename, indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = 'public'
        ORDER BY tablename, indexname
        """
    )
    constraints = await pool.fetch(
        """
        SELECT conrelid::regclass::text AS table_name, conname,
               pg_get_constraintdef(oid) AS definition
        FROM pg_constraint
        WHERE connamespace = 'public'::regnamespace
        ORDER BY 1, 2
        """
    )
    return {
        "columns": [tuple(r) for r in columns],
        "indexes": [tuple(r) for r in indexes],
        "constraints": [tuple(r) for r in constraints],
    }


async def test_init_db_is_idempotent(pg_db):
    """A restart re-runs every migration; it must not fail or change the schema."""
    before = await _schema_snapshot(pg_db)

    await connection.close_db()
    await connection.init_db()

    after = await _schema_snapshot(connection.get_pool())
    assert after == before


async def test_fresh_install_has_columns_the_write_path_needs(pg_db):
    """schema.sql + init_db() together must provide every column write_event inserts."""
    rows = await pg_db.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'events'
        """
    )
    columns = {r["column_name"] for r in rows}
    assert {
        "tenant_id", "agent_id", "event_type", "data", "parent_event_id",
        "session_id", "checksum", "metadata", "index_status",
    } <= columns


async def test_restart_keeps_existing_rows(pg_db):
    tenant_id = await pg_db.fetchval(
        "INSERT INTO tenants (name) VALUES ('acme') RETURNING tenant_id"
    )

    await connection.close_db()
    await connection.init_db()

    name = await connection.get_pool().fetchval(
        "SELECT name FROM tenants WHERE tenant_id = $1", tenant_id
    )
    assert name == "acme"
