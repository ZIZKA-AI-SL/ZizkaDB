"""Mock-based tests verifying that deleted/forgotten records do not
appear in search results (the 'ghost records' fix).

Uses the same monkeypatch + TestClient pattern as the existing
test_event_write.py and test_api_key_limit_endpoints.py tests.
"""

import datetime
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


# ---------------------------------------------------------------------------
# Shared in-memory store that coordinates mocked Postgres + Qdrant state
# ---------------------------------------------------------------------------

class _InMemoryStore:
    """Tracks events in simulated Postgres and Qdrant stores."""

    def __init__(self):
        self.postgres: dict[str, dict] = {}
        self.qdrant: dict[str, dict] = {}

    def clear(self):
        self.postgres.clear()
        self.qdrant.clear()


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
    import json as _json
    data = row.get("data", "{}")
    if isinstance(data, str):
        data = _json.loads(data)
    return str(data.get(key)) == value


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _bypass_auth():
    """Bypass get_tenant for all tests in this module."""
    app.dependency_overrides[get_tenant] = lambda: {
        "tenant_id": _TENANT_ID,
        "user_id": _USER_ID,
    }
    yield
    app.dependency_overrides.pop(get_tenant, None)


@pytest.fixture()
def store():
    s = _InMemoryStore()
    yield s
    s.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGhostRecords:
    """Forgotten records must not leak into subsequent search results."""

    def _patch_all(self, monkeypatch, store):
        """Set up all mocks for a single test invocation.

        Returns (mock_pool, mock_qdrant) for assertions.
        """
        # -- generate_embedding (OpenAI) -> deterministic fixed vector --------
        monkeypatch.setattr(
            "api.search.generate_embedding",
            AsyncMock(return_value=_FIXED_EMBEDDING),
        )
        monkeypatch.setattr(
            "services.event_write.generate_embedding",
            AsyncMock(return_value=_FIXED_EMBEDDING),
        )
        monkeypatch.setattr(
            "services.event_write.event_to_text",
            lambda event, data: "test",
        )

        # -- Postgres pool ---------------------------------------------------
        pool = AsyncMock()

        async def pool_fetchrow(query, *args):
            """INSERT ... RETURNING -> insert into store and return the row."""
            q = " ".join(query.split())
            if "INSERT INTO events" in q:
                event_id = str(uuid.uuid4())
                row = _make_row(
                    event_id,
                    args[0],  # tenant_id
                    args[1],  # agent
                    args[2],  # event_type
                    args[3],  # data (json string)
                )
                store.postgres[event_id] = row
                return row
            if "nextval" in q:
                # write_event precomputes the canonical row (checksum preflight);
                # return a deterministic row so checksum computation doesn't crash.
                return {
                    "sequence_no": 1,
                    "timestamp": datetime.datetime(
                        2026, 1, 1, tzinfo=datetime.timezone.utc
                    ),
                    "data": args[0],
                    "metadata": args[1],
                    "parent_event_id": args[2],
                }
            return None

        async def pool_fetch(query, *args):
            q = " ".join(query.split())  # collapse whitespace for matching
            if "SELECT event_id FROM events" in q and "data" in q:
                # Forget query: SELECT ... WHERE tenant_id=$1 AND data->$2 = to_jsonb($3::text)
                tenant_match = str(args[0])
                if len(args) >= 3:
                    filter_key = args[1]
                    filter_value = str(args[2])
                    return [
                        {"event_id": r["event_id"]}
                        for r in store.postgres.values()
                        if str(r["tenant_id"]) == tenant_match
                        and _data_matches(r, filter_key, filter_value)
                    ]
                return [
                    {"event_id": r["event_id"]}
                    for r in store.postgres.values()
                    if str(r["tenant_id"]) == tenant_match
                ]
            if "SELECT event_id, agent_id" in q:
                event_ids = set(str(e) for e in args[0])
                return [
                    r for r in store.postgres.values()
                    if str(r["event_id"]) in event_ids
                ]
            return []

        async def pool_execute(query, *args):
            q = " ".join(query.split())
            if "DELETE FROM events" in q:
                ids_to_delete = args[1] if len(args) > 1 else []
                for eid in list(store.postgres.keys()):
                    if store.postgres[eid]["event_id"] in ids_to_delete:
                        del store.postgres[eid]
            return "DELETE 0"

        pool.fetchrow = AsyncMock(side_effect=pool_fetchrow)
        pool.fetch = AsyncMock(side_effect=pool_fetch)
        pool.execute = AsyncMock(side_effect=pool_execute)

        def _acquire_ctx():
            class _Conn:
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *a):
                    return False

                def transaction(self):
                    class _Tx:
                        async def __aenter__(self):
                            return self
                        async def __aexit__(self, *a):
                            return False
                    return _Tx()

                async def execute(self, q, *a):
                    return await pool_execute(q, *a)
                async def fetchrow(self, q, *a):
                    return await pool_fetchrow(q, *a)
                async def fetch(self, q, *a):
                    return await pool_fetch(q, *a)
            return _Conn()

        pool.acquire = _acquire_ctx

        monkeypatch.setattr("services.event_write.get_pool", lambda: pool)
        monkeypatch.setattr("api.search.get_pool", lambda: pool)
        monkeypatch.setattr("api.memory.get_pool", lambda: pool)

        # -- Qdrant client ---------------------------------------------------
        qdrant = AsyncMock()

        async def qdrant_upsert(**kwargs):
            for pt in kwargs.get("points", []):
                store.qdrant[str(pt.id)] = {
                    "id": str(pt.id),
                    "vector": pt.vector,
                    "payload": pt.payload,
                }

        async def qdrant_search(**kwargs):
            filt = kwargs.get("query_filter")
            results = []
            for pt in store.qdrant.values():
                payload = pt["payload"]
                if filt:
                    match = True
                    for cond in filt.must:
                        if payload.get(cond.key) != cond.match.value:
                            match = False
                            break
                    if not match:
                        continue
                results.append(
                    type("Result", (), {"id": pt["id"], "score": 0.99})()
                )
            return results

        async def qdrant_delete(**kwargs):
            selector = kwargs.get("points_selector")
            if selector and hasattr(selector, "points"):
                for pid in selector.points:
                    store.qdrant.pop(str(pid), None)

        qdrant.upsert = AsyncMock(side_effect=qdrant_upsert)
        qdrant.search = AsyncMock(side_effect=qdrant_search)
        qdrant.delete = AsyncMock(side_effect=qdrant_delete)

        monkeypatch.setattr("services.event_write.get_qdrant", lambda: qdrant)
        monkeypatch.setattr("api.search.get_qdrant", lambda: qdrant)
        monkeypatch.setattr("api.memory.get_qdrant", lambda: qdrant)

        # embeddings + synchronous indexing are required for the write ->
        # search -> forget round trip exercised in this module
        monkeypatch.setenv("EMBED_SYNC", "true")
        monkeypatch.setattr("api.memory.embeddings_enabled", lambda: True)
        monkeypatch.setattr("api.search.embeddings_enabled", lambda: True)
        monkeypatch.setattr("services.event_write.embeddings_enabled", lambda: True)

        return pool, qdrant

    def _create_event(self, agent, event, data):
        resp = client.post(
            "/v1/events",
            headers={"Authorization": "Bearer zizkadb_dev_local"},
            json={"agent": agent, "event": event, "data": data},
        )
        assert resp.status_code == 201
        return resp.json()["event_id"]

    def test_search_returns_event_then_forget_removes_it(
        self, monkeypatch, store
    ):
        pool, qdrant = self._patch_all(monkeypatch, store)

        # 1. Create an event
        event_id = self._create_event(
            "ghost-test-agent", "tool_call",
            {"tool": "search", "query": "weather"},
        )

        # 2. Search — the event must be found
        resp = client.post(
            "/v1/search",
            headers={"Authorization": "Bearer zizkadb_dev_local"},
            json={"query": "weather", "agent": "ghost-test-agent", "limit": 10},
        )
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) == 1
        assert results[0]["event_id"] == event_id

        # 3. Forget the event by tool metadata
        resp = client.request(
            "DELETE",
            "/v1/memory/forget",
            headers={"Authorization": "Bearer zizkadb_dev_local"},
            json={"filter_key": "tool", "filter_value": "search"},
        )
        assert resp.status_code == 200
        assert resp.json()["vector_cleanup"] is True
        assert resp.json()["deleted_events"] >= 1

        # 4. Search again — ghost record must NOT appear
        resp = client.post(
            "/v1/search",
            headers={"Authorization": "Bearer zizkadb_dev_local"},
            json={"query": "weather", "limit": 10},
        )
        assert resp.status_code == 200
        results = resp.json()["results"]
        found_ids = [r["event_id"] for r in results]
        assert event_id not in found_ids

    def test_forget_returns_vector_cleanup_flag(self, monkeypatch, store):
        """The response must include vector_cleanup: true on success."""
        self._patch_all(monkeypatch, store)

        # Create an event so the forget endpoint doesn't short-circuit
        self._create_event(
            "agent-forget", "message",
            {"user_id": "user_999", "text": "hi"},
        )

        resp = client.request(
            "DELETE",
            "/v1/memory/forget",
            headers={"Authorization": "Bearer zizkadb_dev_local"},
            json={"filter_key": "user_id", "filter_value": "user_999"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "vector_cleanup" in body
        assert body["vector_cleanup"] is True
        assert body["deleted_events"] >= 1

    def test_forget_qdrant_failure_reports_cleanup_false(
        self, monkeypatch, store
    ):
        """When Qdrant delete fails all 3 retries with embeddings disabled,
        forget reports vector_cleanup: False instead of raising 503."""
        pool, qdrant = self._patch_all(monkeypatch, store)

        # Create an event so forget actually tries to delete
        self._create_event(
            "agent-fail", "message",
            {"user_id": "user_fail", "text": "oops"},
        )

        # embeddings disabled -> forget reports cleanup=False rather than 503
        monkeypatch.setattr("api.memory.embeddings_enabled", lambda: False)

        # Now make qdrant.delete fail
        qdrant.delete = AsyncMock(
            side_effect=RuntimeError("Qdrant unavailable")
        )

        resp = client.request(
            "DELETE",
            "/v1/memory/forget",
            headers={"Authorization": "Bearer zizkadb_dev_local"},
            json={"filter_key": "user_id", "filter_value": "user_fail"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["vector_cleanup"] is False
        assert "unreachable" in body["message"]
        assert body["deleted_events"] >= 1

    def test_search_returns_empty_after_full_delete(self, monkeypatch, store):
        """After creating and then deleting an event, search returns empty."""
        pool, qdrant = self._patch_all(monkeypatch, store)

        # Create
        event_id = self._create_event(
            "agent-x", "message",
            {"text": "hello"},
        )

        # Verify it exists via search
        resp = client.post(
            "/v1/search",
            headers={"Authorization": "Bearer zizkadb_dev_local"},
            json={"query": "hello", "agent": "agent-x", "limit": 10},
        )
        assert len(resp.json()["results"]) == 1

        # Forget
        resp = client.request(
            "DELETE",
            "/v1/memory/forget",
            headers={"Authorization": "Bearer zizkadb_dev_local"},
            json={"filter_key": "text", "filter_value": "hello"},
        )
        assert resp.status_code == 200
        assert resp.json()["deleted_events"] == 1

        # Search again
        resp = client.post(
            "/v1/search",
            headers={"Authorization": "Bearer zizkadb_dev_local"},
            json={"query": "hello", "agent": "agent-x", "limit": 10},
        )
        assert resp.json()["results"] == []
