"""Tenant isolation with real SQL: two tenants, same agent name, one database."""

import httpx
import pytest
from fastapi import HTTPException

from api.deps import get_tenant
from main import app
from services.event_write import write_event

pytestmark = pytest.mark.postgres

AGENT = "support-bot"


async def _new_tenant(pool, name: str) -> str:
    tenant_id = await pool.fetchval(
        "INSERT INTO tenants (name) VALUES ($1) RETURNING tenant_id", name
    )
    return str(tenant_id)


@pytest.fixture
async def two_tenants(pg_db):
    """Each tenant logs a two-event causal chain under the same agent name."""
    tenants = {}
    for name in ("alpha", "beta"):
        tenant_id = await _new_tenant(pg_db, name)
        root = await write_event(
            tenant_id=tenant_id,
            agent=AGENT,
            event="user_message",
            data={"owner": name, "step": "root"},
            session_id="shared-session-id",
        )
        child = await write_event(
            tenant_id=tenant_id,
            agent=AGENT,
            event="tool_call",
            data={"owner": name, "step": "child"},
            parent_id=root["event_id"],
            session_id="shared-session-id",
        )
        tenants[name] = {
            "tenant_id": tenant_id,
            "root": root["event_id"],
            "child": child["event_id"],
        }
    return tenants


@pytest.fixture
async def api_as():
    """Call the real app with ``get_tenant`` overridden.

    ``api_as(tenant_id)`` switches the caller identity and returns the one
    shared client, so the most recent call decides who every request runs as.
    """
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )

    def _as(tenant_id: str, agent_id: str | None = None) -> httpx.AsyncClient:
        tenant = {"tenant_id": tenant_id, "user_id": None}
        if agent_id:
            tenant["agent_id"] = agent_id
        app.dependency_overrides[get_tenant] = lambda: tenant
        return client

    try:
        yield _as
    finally:
        app.dependency_overrides.pop(get_tenant, None)
        await client.aclose()


async def test_event_list_returns_only_the_callers_events(two_tenants, api_as):
    for name, other in (("alpha", "beta"), ("beta", "alpha")):
        client = api_as(two_tenants[name]["tenant_id"])
        response = await client.get("/v1/events", params={"agent": AGENT})

        assert response.status_code == 200
        events = response.json()
        assert {e["event_id"] for e in events} == {
            two_tenants[name]["root"],
            two_tenants[name]["child"],
        }
        assert all(e["data"]["owner"] == name for e in events)
        assert two_tenants[other]["root"] not in {e["event_id"] for e in events}


async def test_why_walks_the_callers_chain_on_real_postgres(two_tenants, api_as):
    alpha = two_tenants["alpha"]
    client = api_as(alpha["tenant_id"])

    response = await client.get(f"/v1/events/{alpha['child']}/why")

    assert response.status_code == 200
    body = response.json()
    assert body["chain_length"] == 2
    # Root cause first, anchor last.
    assert [e["event_id"] for e in body["chain"]] == [alpha["root"], alpha["child"]]


async def test_why_hides_another_tenants_event(two_tenants, api_as):
    client = api_as(two_tenants["beta"]["tenant_id"])

    response = await client.get(f"/v1/events/{two_tenants['alpha']['child']}/why")

    assert response.status_code == 404


async def test_parent_from_another_tenant_is_rejected(two_tenants, pg_db):
    beta = two_tenants["beta"]
    before = await pg_db.fetchval(
        "SELECT COUNT(*) FROM events WHERE tenant_id = $1", beta["tenant_id"]
    )

    with pytest.raises(HTTPException) as exc:
        await write_event(
            tenant_id=beta["tenant_id"],
            agent=AGENT,
            event="tool_call",
            data={"owner": "beta"},
            parent_id=two_tenants["alpha"]["root"],
        )

    assert exc.value.status_code == 400
    after = await pg_db.fetchval(
        "SELECT COUNT(*) FROM events WHERE tenant_id = $1", beta["tenant_id"]
    )
    assert after == before


async def test_scoped_key_cannot_walk_another_agents_chain(two_tenants, api_as):
    alpha = two_tenants["alpha"]
    other = await write_event(
        tenant_id=alpha["tenant_id"],
        agent="billing-bot",
        event="tool_call",
        data={"owner": "alpha"},
    )
    url = f"/v1/events/{other['event_id']}/why"

    # Control: the event is reachable without the agent scope.
    unscoped = await api_as(alpha["tenant_id"]).get(url)
    assert unscoped.status_code == 200

    scoped = await api_as(alpha["tenant_id"], agent_id=AGENT).get(url)
    assert scoped.status_code == 404
