"""Who wrote each event (sealed into it), and why() across agents for scoped keys."""

import httpx
import pytest

from api.deps import get_tenant, get_writer_tenant
from main import app
from services.writer import seal_writer, writer_of

pytestmark = pytest.mark.postgres

PLANNER = "planner"
BILLING = "billing"


async def _new_tenant(pool, name: str) -> str:
    return str(await pool.fetchval("INSERT INTO tenants (name) VALUES ($1) RETURNING tenant_id", name))


@pytest.fixture
async def api(pg_db, monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "managed")
    tenant_id = await _new_tenant(pg_db, "acme")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    def as_(who: str | None) -> httpx.AsyncClient:
        """An API key bound to agent ``who``; None is a dashboard session."""
        if who is None:
            tenant = {"tenant_id": tenant_id, "user_id": "user-1", "auth": "dashboard"}
        else:
            tenant = {
                "tenant_id": tenant_id,
                "key_id": f"key-{who}",
                "key_prefix": f"zizkadb_live_{who[:4]}",
                "agent_id": who,
                "scope": "read_write",
            }
        app.dependency_overrides[get_tenant] = lambda: tenant
        app.dependency_overrides[get_writer_tenant] = lambda: tenant
        return client

    try:
        yield as_, tenant_id
    finally:
        app.dependency_overrides.clear()
        await client.aclose()


async def _log(client, agent, event, parent=None, session="case-1", metadata=None):
    body = {"agent": agent, "event": event, "data": {"step": event}, "session_id": session}
    if parent:
        body["parent_id"] = parent
    if metadata is not None:
        body["metadata"] = metadata
    r = await client.post("/v1/events", json=body)
    assert r.status_code == 201, r.text
    return r.json()["event_id"]


async def _metadata(pool, event_id):
    import json

    raw = await pool.fetchval("SELECT metadata FROM events WHERE event_id = $1", event_id)
    return json.loads(raw) if isinstance(raw, str) else raw


# ── written_by ────────────────────────────────────────────────────────────


async def test_an_api_key_write_records_the_key(api, pg_db):
    as_, _ = api
    eid = await _log(as_(PLANNER), PLANNER, "user_message")

    meta = await _metadata(pg_db, eid)
    assert meta["written_by"] == {"kind": "api_key", "key_id": "key-planner", "key_prefix": "zizkadb_live_plan"}


async def test_a_client_cannot_supply_or_fake_the_writer(api, pg_db):
    as_, _ = api
    fake = {"kind": "api_key", "key_id": "someone-else"}
    eid = await _log(as_(PLANNER), PLANNER, "step", metadata={"written_by": fake, "written_by_original": fake, "team": "a"})

    meta = await _metadata(pg_db, eid)
    assert meta["written_by"]["key_id"] == "key-planner"
    assert "written_by_original" not in meta
    assert meta["team"] == "a"


async def test_a_dashboard_write_is_labelled_as_the_dashboard_user(api, pg_db):
    as_, _ = api
    eid = await _log(as_(None), BILLING, "manual_note")

    meta = await _metadata(pg_db, eid)
    assert meta["written_by"] == {"kind": "dashboard", "user_id": "user-1"}


async def test_the_writer_is_sealed_by_the_checksum_and_chain(api, pg_db):
    as_, _ = api
    client = as_(PLANNER)
    first = await _log(client, PLANNER, "user_message")
    await _log(client, PLANNER, "tool_call", parent=first)

    verify = await client.get(f"/v1/events/{first}/verify")
    chain = await client.get("/v1/events/chain", params={"agent": PLANNER})
    assert verify.json()["status"] == "valid"
    assert chain.json()["intact"] is True

    # Changing who wrote it afterwards breaks the event's checksum.
    await pg_db.execute(
        """UPDATE events SET metadata = jsonb_set(metadata, '{written_by,key_id}', '"key-billing"')
           WHERE event_id = $1""",
        first,
    )
    assert (await client.get(f"/v1/events/{first}/verify")).json()["status"] == "mismatch"


async def test_an_import_keeps_the_original_writer(api, pg_db):
    as_, _ = api
    original = {"kind": "api_key", "key_id": "old-server-key"}
    r = await as_(PLANNER).post("/v1/events/import", json={"events": [{
        "event_id": "e-1", "agent": PLANNER, "event": "user_message", "data": {},
        "metadata": {"written_by": original},
    }]})
    assert r.json()["imported"] == 1
    eid = await pg_db.fetchval("SELECT event_id FROM events WHERE idempotency_key = 'import:e-1'")

    meta = await _metadata(pg_db, eid)
    assert meta["written_by"]["key_id"] == "key-planner"
    assert meta["written_by_original"] == original


def test_writer_of_each_credential():
    assert writer_of({"tenant_id": "t", "key_id": "k", "key_prefix": "p"}) == {"kind": "api_key", "key_id": "k", "key_prefix": "p"}
    assert writer_of({"tenant_id": "t", "user_id": "u", "auth": "dev_key"}) == {"kind": "dev_key"}
    assert writer_of({"tenant_id": "t", "user_id": "u", "auth": "dashboard"}) == {"kind": "dashboard", "user_id": "u"}
    assert seal_writer(None, None) is None


# ── why() across agents ───────────────────────────────────────────────────


@pytest.fixture
async def handoff(api):
    """planner: user_message → handoff; billing: tool_error caused by the handoff."""
    as_, _ = api
    root = await _log(as_(PLANNER), PLANNER, "user_message")
    sent = await _log(as_(PLANNER), PLANNER, "a2a.message", parent=root)
    failed = await _log(as_(BILLING), BILLING, "tool_error", parent=sent)
    return {"root": root, "sent": sent, "failed": failed}


async def test_a_scoped_key_is_told_the_chain_continues_in_another_agent(api, handoff):
    as_, _ = api
    body = (await as_(BILLING).get(f"/v1/events/{handoff['failed']}/why")).json()

    assert [e["agent"] for e in body["chain"]] == [BILLING]
    assert body["scoped_agent_limited"] is True
    assert body["chain_complete"] is False
    assert body["continues_in"] == {"agent": PLANNER, "event_id": handoff["sent"]}
    assert "data" not in body["continues_in"]


async def test_the_dashboard_sees_the_whole_chain_across_agents(api, handoff):
    as_, _ = api
    body = (await as_(None).get(f"/v1/events/{handoff['failed']}/why")).json()

    assert [e["agent"] for e in body["chain"]] == [PLANNER, PLANNER, BILLING]
    assert body["scoped_agent_limited"] is False
    assert body["continues_in"] is None


async def test_session_why_reports_the_continuation_too(api, handoff):
    as_, _ = api
    body = (await as_(BILLING).get(f"/v1/sessions/case-1/why/{handoff['failed']}")).json()

    assert body["scoped_agent_limited"] is True
    assert body["chain_complete"] is False
    assert body["continues_in"]["agent"] == PLANNER


async def test_a_chain_within_one_agent_is_unchanged(api, handoff):
    as_, _ = api
    body = (await as_(PLANNER).get(f"/v1/events/{handoff['sent']}/why")).json()

    assert [e["agent"] for e in body["chain"]] == [PLANNER, PLANNER]
    assert body["scoped_agent_limited"] is False
    assert body["continues_in"] is None
