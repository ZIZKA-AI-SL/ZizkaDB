"""Agent names against a real Postgres: a new agent must be reachable through
/v1/agents/{id}; agents stored before the rule keep working."""

import pytest
from fastapi import HTTPException

from api.deps import assert_agent_allowed
from services.api_keys import create_api_key_record
from services.event_write import write_event

pytestmark = pytest.mark.postgres


async def _tenant(pool) -> str:
    return str(await pool.fetchval(
        "INSERT INTO tenants (name) VALUES ('acme') RETURNING tenant_id"
    ))


async def _count(pool, table: str, tenant_id: str) -> int:
    return await pool.fetchval(f"SELECT COUNT(*) FROM {table} WHERE tenant_id = $1", tenant_id)


async def test_new_agent_name_no_route_can_reach_writes_no_agent_or_event(pg_db):
    tenant_id = await _tenant(pg_db)

    with pytest.raises(HTTPException) as exc:
        await write_event(tenant_id=tenant_id, agent="team/bot", event="ping", data={})

    assert exc.value.status_code == 400
    assert await _count(pg_db, "agents", tenant_id) == 0
    assert await _count(pg_db, "events", tenant_id) == 0


async def test_valid_agent_name_is_written(pg_db):
    tenant_id = await _tenant(pg_db)

    await write_event(tenant_id=tenant_id, agent="a.b_c-1", event="ping", data={})

    assert await pg_db.fetchval(
        "SELECT agent_id FROM agents WHERE tenant_id = $1", tenant_id
    ) == "a.b_c-1"


async def test_unbound_key_is_not_bound_to_a_name_no_route_can_reach(pg_db):
    tenant_id = await _tenant(pg_db)
    key = await create_api_key_record(pg_db, tenant_id=tenant_id, name="first")
    tenant = {"tenant_id": tenant_id, "key_id": key["key_id"], "agent_id": None}

    with pytest.raises(HTTPException) as exc:
        await assert_agent_allowed(tenant, "my bot")

    assert exc.value.status_code == 400
    assert await _count(pg_db, "agents", tenant_id) == 0
    assert await pg_db.fetchval(
        "SELECT agent_id FROM api_keys WHERE key_id = $1::uuid", key["key_id"]
    ) is None

    # The key is still free for a valid name.
    await assert_agent_allowed(tenant, "my-bot")
    assert tenant["agent_id"] == "my-bot"


async def test_agent_stored_before_the_rule_keeps_working(pg_db):
    tenant_id = await _tenant(pg_db)
    await pg_db.execute(
        "INSERT INTO agents (agent_id, tenant_id) VALUES ('legacy bot', $1)", tenant_id
    )
    key = await create_api_key_record(pg_db, tenant_id=tenant_id, name="first")
    tenant = {"tenant_id": tenant_id, "key_id": key["key_id"], "agent_id": None}

    await assert_agent_allowed(tenant, "legacy bot")
    await write_event(tenant_id=tenant_id, agent="legacy bot", event="ping", data={})

    assert tenant["agent_id"] == "legacy bot"
    assert await _count(pg_db, "agents", tenant_id) == 1
    assert await _count(pg_db, "events", tenant_id) == 1


@pytest.mark.parametrize(
    "method,agent",
    [
        ("post", "my bot"),
        ("post", "a\x00b"),
        ("get", "my bot"),
        ("get", "a\x00b"),
        ("get", "a" * 300),
    ],
)
async def test_unassigned_key_over_http_gets_400_for_an_unreachable_name(pg_db, method, agent):
    """The full route: get_tenant, then key binding, then the write."""
    import json

    import httpx

    from main import app

    tenant_id = await _tenant(pg_db)
    key = await create_api_key_record(pg_db, tenant_id=tenant_id, name="first")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {key['key']}", "content-type": "application/json"},
    ) as client:
        if method == "post":
            # json.dumps keeps the NUL and the lone surrogate as JSON escapes.
            body = json.dumps({"agent": agent, "event": "ping", "data": {}})
            response = await client.post("/v1/events", content=body)
        else:
            response = await client.get("/v1/events", params={"agent": agent})

    assert response.status_code == 400, response.text
    assert await _count(pg_db, "agents", tenant_id) == 0
    assert await pg_db.fetchval(
        "SELECT agent_id FROM api_keys WHERE key_id = $1::uuid", key["key_id"]
    ) is None
