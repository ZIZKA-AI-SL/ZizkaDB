"""Read-only and expiring API keys, against a real Postgres."""

import httpx
import pytest

from services.api_keys import count_active_api_keys, create_api_key_record

pytestmark = pytest.mark.postgres

AGENT = "bot"


async def _setup(pool):
    tenant_id = str(await pool.fetchval("INSERT INTO tenants (name) VALUES ('acme') RETURNING tenant_id"))
    await pool.execute("INSERT INTO agents (agent_id, tenant_id) VALUES ($1, $2)", AGENT, tenant_id)
    return tenant_id


def _client() -> httpx.AsyncClient:
    from main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_read_only_key_reads_but_never_writes(pg_db, monkeypatch):
    monkeypatch.setattr("api.search.embeddings_enabled", lambda: False)
    tenant_id = await _setup(pg_db)
    rw = await create_api_key_record(pg_db, tenant_id=tenant_id, name="rw", agent_id=AGENT)
    ro = await create_api_key_record(pg_db, tenant_id=tenant_id, name="ro", agent_id=AGENT,
                                     scope="read_only")
    auth_rw = {"Authorization": f"Bearer {rw['key']}"}
    auth_ro = {"Authorization": f"Bearer {ro['key']}"}
    event = {"agent": AGENT, "event": "e", "data": {"x": "refund"}}

    async with _client() as client:
        r = await client.post("/v1/events", json=event, headers=auth_rw)
        assert r.status_code == 201

        denied = await client.post("/v1/events", json=event, headers=auth_ro)
        assert denied.status_code == 403 and "read-only" in denied.json()["detail"]
        r = await client.post("/v1/events/batch", json={"events": [event]}, headers=auth_ro)
        assert r.status_code == 403
        r = await client.request("DELETE", "/v1/memory/forget", headers=auth_ro,
                                 json={"filter_key": "x", "filter_value": "refund"})
        assert r.status_code == 403

        r = await client.get(f"/v1/events?agent={AGENT}", headers=auth_ro)
        assert r.status_code == 200
        r = await client.post("/v1/search", json={"query": "refund"}, headers=auth_ro)
        assert r.status_code == 200
        r = await client.get(f"/v1/events/chain?agent={AGENT}", headers=auth_ro)
        assert r.json()["intact"] is True
    assert await pg_db.fetchval("SELECT COUNT(*) FROM events") == 1


async def test_expired_key_is_rejected_and_frees_its_slot(pg_db):
    tenant_id = await _setup(pg_db)
    key = await create_api_key_record(pg_db, tenant_id=tenant_id, name="tmp", agent_id=AGENT,
                                      expires_in_days=1)
    assert key["expires_at"] is not None
    auth = {"Authorization": f"Bearer {key['key']}"}
    async with _client() as client:
        assert (await client.get(f"/v1/events?agent={AGENT}", headers=auth)).status_code == 200
        async with pg_db.acquire() as conn:
            assert await count_active_api_keys(conn, tenant_id) == 1

        await pg_db.execute("UPDATE api_keys SET expires_at = NOW() - interval '1 second'")
        assert (await client.get(f"/v1/events?agent={AGENT}", headers=auth)).status_code == 401
    async with pg_db.acquire() as conn:
        assert await count_active_api_keys(conn, tenant_id) == 0
