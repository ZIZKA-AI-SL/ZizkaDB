"""Mock-based round trip for 'ghost records': an event written through
POST /v1/events is found by POST /v1/search, and DELETE /v1/memory/forget
removes its row and its vector, so nothing is left in the search index.

Postgres and Qdrant are one in-memory store and OpenAI is a fixed embedding.
The tests assert on the fake index itself: search reads every hit back from
Postgres, so a leftover vector never shows up in results and checking the
search response alone would not catch one. The forget path's locks, retries
and ordering are covered call by call in test_forget_and_health.py.
"""

import datetime
import json
import types
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from main import app
from api.deps import get_tenant

client = TestClient(app)

_TENANT_ID = "00000000-0000-0000-0000-000000000001"
_USER_ID = "00000000-0000-0000-0000-000000000002"
_FIXED_EMBEDDING = [0.1] * 1536
_AUTH = {"Authorization": "Bearer zizkadb_dev_local"}


class _InMemoryStore:
    """Events as simulated Postgres rows and Qdrant points, keyed by event id."""

    def __init__(self):
        self.postgres: dict[str, dict] = {}
        self.qdrant: dict[str, dict] = {}


def _make_row(event_id, tenant_id, agent_id, event_type, data):
    return {
        "event_id": uuid.UUID(event_id),
        "tenant_id": uuid.UUID(tenant_id),
        "agent_id": agent_id,
        "event_type": event_type,
        "data": data,
        "parent_event_id": None,
        "session_id": None,
        "sequence_no": 1,
        "timestamp": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        "embedding": _FIXED_EMBEDDING,
    }


def _data_matches(row, key, value):
    """Simulate PostgreSQL JSONB query: data->$key = to_jsonb($value::text)."""
    data = row.get("data", "{}")
    if isinstance(data, str):
        data = json.loads(data)
    return str(data.get(key)) == value


@pytest.fixture(autouse=True)
def _bypass_auth():
    """A dashboard-style tenant: tenant-wide, no API key binding."""
    app.dependency_overrides[get_tenant] = lambda: {
        "tenant_id": _TENANT_ID,
        "user_id": _USER_ID,
    }
    yield
    app.dependency_overrides.pop(get_tenant, None)


@pytest.fixture()
def store():
    return _InMemoryStore()


def _patch_all(monkeypatch, store):
    """Wire the fakes into the write, search and forget paths.

    Returns the fake Qdrant client, so a test can make its delete fail.
    """
    embed = AsyncMock(return_value=_FIXED_EMBEDDING)
    monkeypatch.setattr("api.search.generate_embedding", embed)
    monkeypatch.setattr("services.event_write.generate_embedding", embed)
    monkeypatch.setattr("services.event_write.event_to_text", lambda event, data: "test")

    async def fetchrow(query, *args):
        q = " ".join(query.split())
        if "INSERT INTO events" in q:
            event_id = str(uuid.uuid4())
            row = _make_row(event_id, args[0], args[1], args[2], args[3])
            store.postgres[event_id] = row
            return row
        if "nextval" in q:
            # write_event precomputes the canonical row for its checksum.
            return {
                "sequence_no": 1,
                "timestamp": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
                "data": args[0],
                "metadata": args[1],
                "parent_event_id": args[2],
            }
        return None

    async def fetch(query, *args):
        q = " ".join(query.split())
        if "DELETE FROM events" in q:
            # delete_and_tombstone(): one statement that deletes the rows and
            # reports how many it removed (no chained rows, so no receipts).
            ids = {str(e) for e in args[1]}
            gone = [eid for eid in store.postgres if eid in ids]
            for eid in gone:
                del store.postgres[eid]
            return [{"deleted": len(gone), "agent_id": None}]
        if "SELECT event_id FROM events" in q and "data->" in q:
            # forget's match: tenant, data->key = value, optional scoped agent.
            tenant, key, value, agent = args
            return [
                {"event_id": r["event_id"]}
                for r in store.postgres.values()
                if str(r["tenant_id"]) == str(tenant)
                and _data_matches(r, key, str(value))
                and (agent is None or r["agent_id"] == agent)
            ]
        if "SELECT event_id, agent_id" in q:
            # search hydrates its hits from Postgres.
            wanted = {str(e) for e in args[0]}
            return [r for r in store.postgres.values() if str(r["event_id"]) in wanted]
        return []

    async def execute(query, *args):
        # Advisory locks and agent row locks: nothing to simulate.
        return "OK"

    class _Tx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Conn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def transaction(self):
            return _Tx()

        async def execute(self, q, *a):
            return await execute(q, *a)

        async def fetchrow(self, q, *a):
            return await fetchrow(q, *a)

        async def fetch(self, q, *a):
            return await fetch(q, *a)

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=fetchrow)
    pool.fetch = AsyncMock(side_effect=fetch)
    pool.execute = AsyncMock(side_effect=execute)
    pool.acquire = _Conn

    for target in ("services.event_write", "api.search", "api.memory"):
        monkeypatch.setattr(f"{target}.get_pool", lambda: pool)

    async def upsert(**kwargs):
        for pt in kwargs.get("points", []):
            store.qdrant[str(pt.id)] = {"id": str(pt.id), "payload": pt.payload}

    async def search(**kwargs):
        filt = kwargs.get("query_filter")
        hits = []
        for pt in store.qdrant.values():
            if filt and any(pt["payload"].get(c.key) != c.match.value for c in filt.must):
                continue
            hits.append(types.SimpleNamespace(id=pt["id"], score=0.99))
        return hits

    async def delete(**kwargs):
        selector = kwargs["points_selector"]
        if not hasattr(selector, "points"):
            raise NotImplementedError(f"fake Qdrant: unsupported selector {selector!r}")
        for pid in selector.points:
            store.qdrant.pop(str(pid), None)

    qdrant = AsyncMock()
    qdrant.upsert = AsyncMock(side_effect=upsert)
    qdrant.search = AsyncMock(side_effect=search)
    qdrant.delete = AsyncMock(side_effect=delete)
    for target in ("services.event_write", "api.search", "api.memory"):
        monkeypatch.setattr(f"{target}.get_qdrant", lambda: qdrant)

    # Embeddings on, indexed synchronously, so the write lands in the fake
    # index before the request returns.
    monkeypatch.setenv("EMBED_SYNC", "true")
    for target in ("services.event_write", "api.search", "api.memory"):
        monkeypatch.setattr(f"{target}.embeddings_enabled", lambda: True)
    # No real back-off between forget's Qdrant attempts.
    monkeypatch.setattr(
        "api.memory.asyncio", types.SimpleNamespace(sleep=AsyncMock())
    )
    return qdrant


def _create_event(agent, event, data):
    resp = client.post(
        "/v1/events", headers=_AUTH, json={"agent": agent, "event": event, "data": data}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["event_id"]


def _search(query, agent=None):
    body = {"query": query, "limit": 10}
    if agent:
        body["agent"] = agent
    resp = client.post("/v1/search", headers=_AUTH, json=body)
    assert resp.status_code == 200, resp.text
    return [r["event_id"] for r in resp.json()["results"]]


def _forget(key, value):
    return client.request(
        "DELETE", "/v1/memory/forget", headers=_AUTH,
        json={"filter_key": key, "filter_value": value},
    )


def test_forget_removes_row_and_vector(monkeypatch, store):
    _patch_all(monkeypatch, store)
    event_id = _create_event("ghost-test-agent", "tool_call", {"tool": "search", "query": "weather"})
    keep_id = _create_event("ghost-test-agent", "tool_call", {"tool": "browse", "query": "weather"})

    # The write indexed both events under their own ids, and search finds them.
    assert set(store.qdrant) == {event_id, keep_id}
    assert set(_search("weather", agent="ghost-test-agent")) == {event_id, keep_id}

    resp = _forget("tool", "search")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["vector_cleanup"] is True
    assert body["deleted_events"] == 1

    # No ghost: the row and the vector are both gone; the other event stays.
    assert event_id not in store.postgres
    assert event_id not in store.qdrant
    assert keep_id in store.postgres and keep_id in store.qdrant
    assert _search("weather") == [keep_id]


def test_forget_keeps_row_and_vector_when_index_is_down(monkeypatch, store):
    """With embeddings on, an unreachable index refuses the forget (503)
    before any row is deleted, so a retry can still find the vector."""
    qdrant = _patch_all(monkeypatch, store)
    event_id = _create_event("agent-fail", "message", {"user_id": "user_fail", "text": "oops"})
    qdrant.delete = AsyncMock(side_effect=RuntimeError("Qdrant unavailable"))

    resp = _forget("user_id", "user_fail")

    assert resp.status_code == 503, resp.text
    assert qdrant.delete.await_count == 3
    assert event_id in store.postgres
    assert event_id in store.qdrant


def test_forget_with_embeddings_off_reports_cleanup_false(monkeypatch, store):
    """Embeddings off: the rows go even when Qdrant is unreachable, and the
    response says the index was not cleaned."""
    qdrant = _patch_all(monkeypatch, store)
    event_id = _create_event("agent-off", "message", {"user_id": "user_off", "text": "hi"})
    monkeypatch.setattr("api.memory.embeddings_enabled", lambda: False)
    qdrant.delete = AsyncMock(side_effect=RuntimeError("Qdrant unavailable"))

    resp = _forget("user_id", "user_off")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["vector_cleanup"] is False
    assert "unreachable" in body["message"]
    assert body["deleted_events"] == 1
    assert event_id not in store.postgres
