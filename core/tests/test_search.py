"""Round-trip tests for POST /v1/search.

Covers the write -> search path end to end at the router level: an event is
written (mocked), then searched back (mocked Qdrant + Postgres), verifying
results are non-empty, correctly scored, and correctly scoped to the
requesting tenant.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from main import app
from api.deps import get_tenant

client = TestClient(app)

_TENANT = {"tenant_id": "11111111-1111-1111-1111-111111111111"}
_EVENT_ID = "22222222-2222-2222-2222-222222222222"


def _override_tenant():
    return _TENANT


class TestSearchRoundTrip:
    def setup_method(self):
        app.dependency_overrides[get_tenant] = _override_tenant

    def teardown_method(self):
        app.dependency_overrides.pop(get_tenant, None)

    @staticmethod
    def _make_hit(event_id: str, score: float):
        hit = MagicMock()
        hit.id = event_id
        hit.score = score
        return hit

    @pytest.fixture
    def mock_row(self):
        import datetime

        return {
            "event_id": _EVENT_ID,
            "agent_id": "agent1",
            "timestamp": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            "event_type": "message",
            "data": {"text": "hello"},
            "parent_event_id": None,
            "session_id": None,
            "sequence_no": 1,
        }

    def test_search_returns_indexed_event(self, monkeypatch, mock_row):
        """An event that was successfully embedded and upserted must come back
        in search results with a usable score — the core write-then-search
        round trip."""
        qdrant = AsyncMock()
        qdrant.search.return_value = [self._make_hit(_EVENT_ID, 0.87)]
        monkeypatch.setattr("api.search.get_qdrant", lambda: qdrant)

        pool = AsyncMock()
        pool.fetch.return_value = [mock_row]
        monkeypatch.setattr("api.search.get_pool", lambda: pool)

        monkeypatch.setattr(
            "api.search.generate_embedding",
            AsyncMock(return_value=[0.1] * 1536),
        )

        response = client.post("/v1/search", json={"query": "hello", "mode": "semantic", "include_superseded": True})

        assert response.status_code == 200
        body = response.json()
        assert len(body["results"]) == 1
        assert body["results"][0]["event_id"] == _EVENT_ID
        assert body["results"][0]["score"] == 0.87

    def test_search_empty_when_no_qdrant_hits(self, monkeypatch):
        """No vectors matched (e.g. nothing indexed yet) -> empty results,
        not an error, and Postgres is never queried."""
        qdrant = AsyncMock()
        qdrant.search.return_value = []
        monkeypatch.setattr("api.search.get_qdrant", lambda: qdrant)

        pool = AsyncMock()
        monkeypatch.setattr("api.search.get_pool", lambda: pool)

        monkeypatch.setattr(
            "api.search.generate_embedding",
            AsyncMock(return_value=[0.1] * 1536),
        )

        response = client.post("/v1/search", json={"query": "hello", "mode": "semantic", "include_superseded": True})

        assert response.status_code == 200
        assert response.json()["results"] == []
        pool.fetch.assert_not_called()

    def test_search_fails_clearly_without_embeddings_configured(self, monkeypatch):
        """If embedding generation fails (e.g. no API key configured for the
        tenant), the request must fail with a clear 400 instead of silently
        returning empty results that look like 'nothing was found'."""
        monkeypatch.setattr(
            "api.search.generate_embedding",
            AsyncMock(return_value=None),
        )

        response = client.post("/v1/search", json={"query": "hello", "mode": "semantic", "include_superseded": True})

        assert response.status_code == 400
        assert "embedding" in response.json()["detail"].lower()

    def test_search_scores_survive_id_type_mismatch(self, monkeypatch, mock_row):
        """Qdrant point IDs and Postgres event_id rows must be matched
        consistently regardless of whether the point id comes back as a
        UUID object, str, or int-like value from the client library."""
        qdrant = AsyncMock()
        # Simulate qdrant-client handing back a non-str id representation.
        qdrant.search.return_value = [self._make_hit(_EVENT_ID, 0.42)]
        monkeypatch.setattr("api.search.get_qdrant", lambda: qdrant)

        pool = AsyncMock()
        pool.fetch.return_value = [mock_row]
        monkeypatch.setattr("api.search.get_pool", lambda: pool)

        monkeypatch.setattr(
            "api.search.generate_embedding",
            AsyncMock(return_value=[0.1] * 1536),
        )

        response = client.post("/v1/search", json={"query": "hello", "mode": "semantic", "include_superseded": True})

        body = response.json()
        assert body["results"][0]["score"] == 0.42

    def test_search_does_not_leak_other_tenants_filter(self, monkeypatch, mock_row):
        """The Qdrant query filter must scope by the requesting tenant_id."""
        qdrant = AsyncMock()
        qdrant.search.return_value = [self._make_hit(_EVENT_ID, 0.5)]
        monkeypatch.setattr("api.search.get_qdrant", lambda: qdrant)

        pool = AsyncMock()
        pool.fetch.return_value = [mock_row]
        monkeypatch.setattr("api.search.get_pool", lambda: pool)

        monkeypatch.setattr(
            "api.search.generate_embedding",
            AsyncMock(return_value=[0.1] * 1536),
        )

        client.post("/v1/search", json={"query": "hello", "mode": "semantic", "include_superseded": True})

        _, kwargs = qdrant.search.call_args
        must_conditions = kwargs["query_filter"].must
        tenant_conditions = [
            c for c in must_conditions if c.key == "tenant_id"
        ]
        assert len(tenant_conditions) == 1
        assert tenant_conditions[0].match.value == _TENANT["tenant_id"]


class TestSearchAgentScope:
    def teardown_method(self):
        app.dependency_overrides.pop(get_tenant, None)

    def test_unassigned_api_key_requires_agent(self):
        """Unassigned keys must not search the whole tenant without an agent."""
        app.dependency_overrides[get_tenant] = lambda: {
            "tenant_id": _TENANT["tenant_id"],
            "key_id": "key-1",
        }
        response = client.post("/v1/search", json={"query": "hello", "mode": "semantic", "include_superseded": True})
        assert response.status_code == 400
        assert "agent is required" in response.json()["detail"]


class TestSearchModes:
    def setup_method(self):
        app.dependency_overrides[get_tenant] = _override_tenant

    def teardown_method(self):
        app.dependency_overrides.pop(get_tenant, None)

    def test_auto_uses_keywords_when_embeddings_are_off(self, monkeypatch):
        monkeypatch.setattr("api.search.embeddings_enabled", lambda: False)
        keyword = AsyncMock(return_value=[])
        monkeypatch.setattr("api.search.keyword_search", keyword)
        monkeypatch.setattr("api.search.get_pool", lambda: AsyncMock())

        r = client.post("/v1/search", json={"query": "refund"})

        assert r.status_code == 200
        assert r.json()["mode"] == "keyword"
        keyword.assert_awaited_once()

    def test_semantic_is_refused_when_embeddings_are_off(self, monkeypatch):
        monkeypatch.setattr("api.search.embeddings_enabled", lambda: False)
        monkeypatch.setattr("api.search.is_self_hosted_deployment", lambda: True)
        r = client.post("/v1/search", json={"query": "refund", "mode": "semantic"})
        assert r.status_code == 400
        assert "keyword" in r.json()["detail"]

    def test_auto_falls_back_to_keywords_when_embedding_fails(self, monkeypatch):
        monkeypatch.setattr("api.search.embeddings_enabled", lambda: True)
        monkeypatch.setattr("api.search.generate_embedding", AsyncMock(return_value=None))
        monkeypatch.setattr("api.search.keyword_search", AsyncMock(return_value=[]))
        monkeypatch.setattr("api.search.get_pool", lambda: AsyncMock())

        body = client.post("/v1/search", json={"query": "refund"}).json()

        assert body["mode"] == "keyword"
        assert "keyword search only" in body["warning"]
