"""GET /v1/events/{id}/impact against real Postgres: the downstream walk."""

import httpx
import pytest

import api.events as events_api
from api.deps import get_tenant
from main import app
from services.event_write import write_event

pytestmark = pytest.mark.postgres

AGENT = "support-bot"
OTHER_AGENT = "billing-bot"


async def _new_tenant(pool, name: str) -> str:
    tenant_id = await pool.fetchval(
        "INSERT INTO tenants (name) VALUES ($1) RETURNING tenant_id", name
    )
    return str(tenant_id)


async def _log(tenant_id: str, event: str, parent: str | None = None, agent: str = AGENT) -> str:
    row = await write_event(
        tenant_id=tenant_id, agent=agent, event=event, data={"step": event}, parent_id=parent
    )
    return row["event_id"]


@pytest.fixture
async def tree(pg_db):
    """root → plan → tool_call → tool_error, and root → reply (a second branch)."""
    tenant_id = await _new_tenant(pg_db, "alpha")
    root = await _log(tenant_id, "user_message")
    plan = await _log(tenant_id, "planning", root)
    tool = await _log(tenant_id, "tool_call", plan)
    error = await _log(tenant_id, "tool_error", tool)
    reply = await _log(tenant_id, "reply", root)
    return {
        "tenant_id": tenant_id,
        "root": root,
        "plan": plan,
        "tool": tool,
        "error": error,
        "reply": reply,
    }


@pytest.fixture
async def api_as():
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


async def test_impact_walks_every_branch_downstream(tree, api_as):
    response = await api_as(tree["tenant_id"]).get(f"/v1/events/{tree['root']}/impact")

    assert response.status_code == 200
    body = response.json()
    assert body["node_count"] == 5
    assert body["truncated"] is False
    depths = {n["event_id"]: n["depth"] for n in body["nodes"]}
    assert depths == {
        tree["root"]: 0,
        tree["plan"]: 1,
        tree["reply"]: 1,
        tree["tool"]: 2,
        tree["error"]: 3,
    }
    # Anchor first, then by depth.
    assert [n["depth"] for n in body["nodes"]] == sorted(depths.values())


async def test_impact_from_a_middle_event_excludes_its_ancestors(tree, api_as):
    response = await api_as(tree["tenant_id"]).get(f"/v1/events/{tree['plan']}/impact")

    assert response.status_code == 200
    ids = [n["event_id"] for n in response.json()["nodes"]]
    assert ids == [tree["plan"], tree["tool"], tree["error"]]


async def test_impact_depth_limits_the_walk(tree, api_as):
    client = api_as(tree["tenant_id"])

    one = await client.get(f"/v1/events/{tree['root']}/impact", params={"depth": 1})
    zero = await client.get(f"/v1/events/{tree['root']}/impact", params={"depth": 0})

    assert {n["event_id"] for n in one.json()["nodes"]} == {
        tree["root"], tree["plan"], tree["reply"]
    }
    assert [n["event_id"] for n in zero.json()["nodes"]] == [tree["root"]]


async def test_impact_rejects_out_of_range_depth(tree, api_as):
    client = api_as(tree["tenant_id"])

    for depth in (-1, 51):
        response = await client.get(
            f"/v1/events/{tree['root']}/impact", params={"depth": depth}
        )
        assert response.status_code == 422


async def test_impact_caps_nodes_and_says_so(tree, api_as, monkeypatch):
    monkeypatch.setattr(events_api, "IMPACT_MAX_NODES", 3)

    response = await api_as(tree["tenant_id"]).get(f"/v1/events/{tree['root']}/impact")

    body = response.json()
    assert body["truncated"] is True
    assert body["node_count"] == 3
    assert [n["depth"] for n in body["nodes"]] == [0, 1, 1]


async def test_impact_hides_another_tenants_event(tree, api_as, pg_db):
    beta = await _new_tenant(pg_db, "beta")

    response = await api_as(beta).get(f"/v1/events/{tree['root']}/impact")

    assert response.status_code == 404


async def test_impact_unknown_or_malformed_id_is_404(tree, api_as):
    client = api_as(tree["tenant_id"])

    malformed = await client.get("/v1/events/not-a-uuid/impact")
    unknown = await client.get("/v1/events/00000000-0000-0000-0000-000000000001/impact")

    assert malformed.status_code == 404
    assert unknown.status_code == 404


async def test_scoped_key_does_not_descend_through_another_agents_event(tree, api_as):
    # tool_call → (billing-bot) charge → (support-bot) follow_up
    charge = await _log(tree["tenant_id"], "charge", tree["tool"], agent=OTHER_AGENT)
    follow_up = await _log(tree["tenant_id"], "follow_up", charge)
    url = f"/v1/events/{tree['tool']}/impact"

    # Control: without the scope the whole subtree is visible.
    unscoped = await api_as(tree["tenant_id"]).get(url)
    assert {n["event_id"] for n in unscoped.json()["nodes"]} >= {charge, follow_up}

    scoped = await api_as(tree["tenant_id"], agent_id=AGENT).get(url)
    assert scoped.status_code == 200
    ids = {n["event_id"] for n in scoped.json()["nodes"]}
    assert ids == {tree["tool"], tree["error"]}


async def test_scoped_key_cannot_anchor_on_another_agents_event(tree, api_as):
    other = await _log(tree["tenant_id"], "charge", agent=OTHER_AGENT)
    url = f"/v1/events/{other}/impact"

    unscoped = await api_as(tree["tenant_id"]).get(url)
    assert unscoped.status_code == 200

    scoped = await api_as(tree["tenant_id"], agent_id=AGENT).get(url)
    assert scoped.status_code == 404
