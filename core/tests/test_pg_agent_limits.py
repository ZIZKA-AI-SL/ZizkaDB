"""Agent cap against a real Postgres: self-hosted is one agent, one API key."""

import pytest
from fastapi import HTTPException

import db.connection as connection
from services.agent_limits import assert_agent_slot, count_agents
from services.api_keys import assert_and_reserve_api_key_slot, create_api_key_record
from services.event_write import write_event

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def _real_collaborators(monkeypatch):
    """Other test modules swap these module attributes for mocks; this file
    exercises the real SQL, so pin the real implementations."""
    import api.agents
    import api.deps
    import services.agent_limits
    import services.api_keys
    import services.auth
    import services.billing

    for module in (api.agents, api.deps, services.auth):
        monkeypatch.setattr(module, "get_pool", connection.get_pool)
    for module in (services.agent_limits, services.api_keys):
        monkeypatch.setattr(module, "fetch_effective_plan", services.billing.fetch_effective_plan)


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


async def _two_old_agents(pool, tenant_id: str) -> None:
    """A tenant that already had two agents before the cap existed."""
    await pool.execute(
        """
        INSERT INTO agents (agent_id, tenant_id, first_seen)
        VALUES ('old-a', $1, NOW() - INTERVAL '2 days'), ('old-b', $1, NOW() - INTERVAL '1 day')
        """,
        tenant_id,
    )


async def test_only_the_oldest_agent_survives_an_upgrade(pg_db):
    """Strict: an install that already had two agents keeps only the first."""
    tenant_id = await _tenant(pg_db)
    await _two_old_agents(pg_db, tenant_id)

    await _log(tenant_id, "old-a")
    for refused in ("old-b", "new-c"):
        with pytest.raises(HTTPException) as exc:
            await _log(tenant_id, refused)
        assert exc.value.status_code == 409
        assert "'old-a'" in exc.value.detail["msg"]


async def test_reads_of_other_agents_are_refused_and_they_are_not_listed(pg_db):
    from api.agents import list_agents
    from api.deps import assert_agent_allowed

    tenant_id = await _tenant(pg_db)
    await _two_old_agents(pg_db, tenant_id)
    await _log(tenant_id, "support-bot")
    dashboard_session = {"tenant_id": tenant_id}  # JWT: no key, no agent scope

    await assert_agent_allowed(dashboard_session, "old-a")
    await assert_agent_allowed(dashboard_session, "support-bot")
    with pytest.raises(HTTPException) as exc:
        await assert_agent_allowed(dashboard_session, "old-b")
    assert exc.value.detail["code"] == "agent_limit_reached"

    listed = {a["agent"] for a in await list_agents(tenant=dashboard_session)}
    assert listed == {"old-a", "support-bot"}


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


async def test_only_the_oldest_api_key_authenticates(pg_db, monkeypatch):
    """Strict: with two active keys (created before the cap), only the first works."""
    from api.deps import resolve_api_key_tenant

    tenant_id = await _tenant(pg_db)
    first = await create_api_key_record(pg_db, tenant_id=tenant_id, name="first")
    second = await create_api_key_record(pg_db, tenant_id=tenant_id, name="second")
    await pg_db.execute(
        "UPDATE api_keys SET created_at = NOW() - INTERVAL '1 day' WHERE key_id = $1::uuid",
        first["key_id"],
    )

    assert (await resolve_api_key_tenant(first["key"]))["tenant_id"] == tenant_id
    with pytest.raises(HTTPException) as exc:
        await resolve_api_key_tenant(second["key"])
    assert exc.value.status_code == 401
    assert "single API key" in str(exc.value.detail)

    # Revoking the older key makes the newer one the key in use.
    await pg_db.execute("UPDATE api_keys SET revoked = TRUE WHERE key_id = $1::uuid", first["key_id"])
    assert (await resolve_api_key_tenant(second["key"]))["tenant_id"] == tenant_id


async def test_managed_keys_are_not_restricted_unless_enforcement_is_on(pg_db, monkeypatch):
    from api.deps import resolve_api_key_tenant

    monkeypatch.setenv("DEPLOYMENT_MODE", "managed")
    monkeypatch.delenv("API_KEY_LIMITS_ENFORCED", raising=False)
    tenant_id = await _tenant(pg_db)
    for name in ("a", "b", "c"):
        key = await create_api_key_record(pg_db, tenant_id=tenant_id, name=name)
        assert await resolve_api_key_tenant(key["key"])
