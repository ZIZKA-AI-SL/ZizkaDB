"""Agent cap against a real Postgres: self-hosted is one agent, one API key."""

import pytest
from fastapi import HTTPException

import db.connection as connection
from services.agent_limits import assert_agent_slot, count_agents
from services.api_keys import assert_and_reserve_api_key_slot, create_api_key_record
from services.event_write import write_event

pytestmark = pytest.mark.postgres


async def _tenant(pool, name="acme") -> str:
    return str(await pool.fetchval(
        "INSERT INTO tenants (name) VALUES ($1) RETURNING tenant_id", name
    ))


async def _log(tenant_id: str, agent: str) -> dict:
    return await write_event(tenant_id=tenant_id, agent=agent, event="ping", data={"ok": True})


async def test_self_hosted_allows_one_agent_and_keeps_serving_it(pg_db):
    tenant_id = await _tenant(pg_db)

    await _log(tenant_id, "my-bot")
    await _log(tenant_id, "my-bot")  # the existing agent is never blocked

    with pytest.raises(HTTPException) as exc:
        await _log(tenant_id, "second-bot")

    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert detail["code"] == "agent_limit_reached"
    assert (detail["plan"], detail["limit"], detail["used"]) == ("self_hosted", 1, 1)
    assert "'my-bot'" in detail["msg"] and "ZizkaDB Cloud" in detail["msg"]
    # Nothing was written for the rejected agent.
    assert await pg_db.fetchval(
        "SELECT COUNT(*) FROM agents WHERE tenant_id = $1", tenant_id
    ) == 1
    assert await pg_db.fetchval(
        "SELECT COUNT(*) FROM events WHERE tenant_id = $1 AND agent_id = 'second-bot'", tenant_id
    ) == 0


async def test_demo_and_connection_test_agents_do_not_use_the_slot(pg_db):
    tenant_id = await _tenant(pg_db)

    await _log(tenant_id, "support-bot")
    await _log(tenant_id, "dashboard-connection-test")
    await _log(tenant_id, "my-bot")

    assert await count_agents(pg_db, tenant_id) == 1
    with pytest.raises(HTTPException):
        await _log(tenant_id, "second-bot")


async def test_agents_that_existed_before_the_cap_keep_working(pg_db):
    tenant_id = await _tenant(pg_db)
    for name in ("old-a", "old-b"):
        await pg_db.execute(
            "INSERT INTO agents (agent_id, tenant_id) VALUES ($1, $2)", name, tenant_id
        )

    await _log(tenant_id, "old-a")
    await _log(tenant_id, "old-b")
    with pytest.raises(HTTPException) as exc:
        await _log(tenant_id, "new-c")
    assert exc.value.detail["used"] == 2


async def test_cap_is_per_tenant(pg_db):
    first, second = await _tenant(pg_db, "one"), await _tenant(pg_db, "two")
    await _log(first, "bot")
    await _log(second, "bot")  # same name, different tenant: its own slot


async def test_managed_mode_is_not_capped_unless_enforcement_is_on(pg_db, monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "managed")
    monkeypatch.delenv("API_KEY_LIMITS_ENFORCED", raising=False)
    tenant_id = await _tenant(pg_db)

    for name in ("a", "b", "c"):
        await _log(tenant_id, name)

    assert await count_agents(pg_db, tenant_id) == 3


async def test_dashboard_create_path_uses_the_same_cap(pg_db):
    tenant_id = await _tenant(pg_db)
    await _log(tenant_id, "my-bot")

    async with connection.get_pool().acquire() as conn:
        async with conn.transaction():
            await assert_agent_slot(conn, tenant_id=tenant_id, agent_id="my-bot")  # exists: ok
            with pytest.raises(HTTPException) as exc:
                await assert_agent_slot(conn, tenant_id=tenant_id, agent_id="second-bot")
    assert exc.value.detail["code"] == "agent_limit_reached"


async def test_self_hosted_allows_one_active_api_key_without_any_env_switch(pg_db, monkeypatch):
    monkeypatch.delenv("API_KEY_LIMITS_ENFORCED", raising=False)
    monkeypatch.setenv("API_KEY_LIMIT_SELF_HOSTED", "99")  # ignored on self-hosted
    tenant_id = await _tenant(pg_db)

    async with connection.get_pool().acquire() as conn:
        async with conn.transaction():
            await assert_and_reserve_api_key_slot(conn, tenant_id=tenant_id)
            await create_api_key_record(conn, tenant_id=tenant_id, name="first")
        async with conn.transaction():
            with pytest.raises(HTTPException) as exc:
                await assert_and_reserve_api_key_slot(conn, tenant_id=tenant_id)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "api_key_limit_reached"
    assert (exc.value.detail["limit"], exc.value.detail["used"]) == (1, 1)


async def test_failed_first_write_leaves_no_agent_row_and_keeps_the_slot(pg_db, monkeypatch):
    """A first write whose event INSERT fails must not leave an agent behind:
    on self-hosted that row would take the single agent slot and every later
    agent would get a 409."""
    tenant_id = await _tenant(pg_db)

    # Input validation now rejects every value the columns cannot hold, so make
    # the write fail after the agent upsert, inside the same transaction.
    def boom(_row):
        raise RuntimeError("write failed after the agent upsert")

    with monkeypatch.context() as m:
        m.setattr("services.event_write.compute_row_checksum_v2", boom)
        with pytest.raises(RuntimeError, match="write failed"):
            await write_event(
                tenant_id=tenant_id,
                agent="first-bot",
                event="ping",
                data={"ok": True},
            )

    assert await pg_db.fetchval(
        "SELECT COUNT(*) FROM agents WHERE tenant_id = $1", tenant_id
    ) == 0
    assert await count_agents(pg_db, tenant_id) == 0

    # The slot is still free: a real first agent can be created.
    await _log(tenant_id, "real-bot")
    assert await count_agents(pg_db, tenant_id) == 1
