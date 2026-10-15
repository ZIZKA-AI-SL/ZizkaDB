"""Regression tests for API-key revoke — malformed key_id must 404, not 500.

Before the fix, revoke_api_key_record passed the raw key_id string into a
$1::uuid cast, so a non-UUID key_id raised inside Postgres
(asyncpg InvalidTextRepresentationError) and surfaced as an unhandled 500
on both DELETE routes. A malformed id can never match a row, so it is a
plain "not found" — the same treatment sibling endpoints give bad UUIDs.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from api.deps import require_dashboard_session
from main import app
from services import api_keys

client = TestClient(app)

_TENANT_ID = "11111111-1111-1111-1111-111111111111"
_USER_ID = "22222222-2222-2222-2222-222222222222"
_KEY_ID = "33333333-3333-3333-3333-333333333333"
_SESSION = {"tenant_id": _TENANT_ID, "user_id": _USER_ID}

MALFORMED_KEY_IDS = ["not-a-uuid", "", "12345"]


def _override_session():
    return _SESSION


def _pool(execute_result: str) -> AsyncMock:
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=execute_result)
    return pool


# ── Service level ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("key_id", MALFORMED_KEY_IDS)
async def test_malformed_key_id_returns_false_without_querying(key_id):
    pool = _pool("UPDATE 1")
    result = await api_keys.revoke_api_key_record(pool, _TENANT_ID, key_id)
    assert result is False
    pool.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_key_id_with_agent_scope_returns_false_without_querying():
    pool = _pool("UPDATE 1")
    result = await api_keys.revoke_api_key_record(pool, _TENANT_ID, "not-a-uuid", agent_id="agent-a")
    assert result is False
    pool.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_uuid_returns_false():
    pool = _pool("UPDATE 0")
    result = await api_keys.revoke_api_key_record(pool, _TENANT_ID, _KEY_ID)
    assert result is False
    pool.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_key_returns_true():
    pool = _pool("UPDATE 1")
    result = await api_keys.revoke_api_key_record(pool, _TENANT_ID, _KEY_ID)
    assert result is True
    pool.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_scoped_existing_key_returns_true():
    pool = _pool("UPDATE 1")
    result = await api_keys.revoke_api_key_record(pool, _TENANT_ID, _KEY_ID, agent_id="agent-a")
    assert result is True
    pool.execute.assert_awaited_once()
    assert pool.execute.await_args.args[1:] == (_KEY_ID, _TENANT_ID, "agent-a")


@pytest.mark.asyncio
async def test_agent_scoped_unknown_uuid_returns_false():
    pool = _pool("UPDATE 0")
    result = await api_keys.revoke_api_key_record(pool, _TENANT_ID, _KEY_ID, agent_id="agent-a")
    assert result is False
    pool.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_urn_prefixed_uuid_is_normalised_before_querying():
    # UUID() accepts the "urn:uuid:" prefix but Postgres's ::uuid cast
    # rejects it — the query must receive the normalised value, or the
    # request still fails at the cast and surfaces as a 500.
    pool = _pool("UPDATE 1")
    result = await api_keys.revoke_api_key_record(pool, _TENANT_ID, f"urn:uuid:{_KEY_ID}")
    assert result is True
    pool.execute.assert_awaited_once()
    assert pool.execute.await_args.args[1:] == (_KEY_ID, _TENANT_ID)


@pytest.mark.asyncio
async def test_urn_prefixed_unknown_uuid_returns_false():
    pool = _pool("UPDATE 0")
    result = await api_keys.revoke_api_key_record(pool, _TENANT_ID, f"urn:uuid:{_KEY_ID}")
    assert result is False
    pool.execute.assert_awaited_once()


# ── Route level ───────────────────────────────────────────────────────────────


class TestRevokeApiKeyRoute:
    def setup_method(self):
        app.dependency_overrides[require_dashboard_session] = _override_session

    def teardown_method(self):
        app.dependency_overrides.pop(require_dashboard_session, None)

    @patch("api.auth.get_pool")
    def test_malformed_key_id_is_404(self, mock_get_pool):
        mock_get_pool.return_value = _pool("UPDATE 1")
        response = client.delete("/v1/auth/api-keys/not-a-uuid")
        assert response.status_code == 404
        assert response.json()["detail"] == "API key not found"

    @patch("api.auth.get_pool")
    def test_existing_key_is_revoked(self, mock_get_pool):
        mock_get_pool.return_value = _pool("UPDATE 1")
        response = client.delete(f"/v1/auth/api-keys/{_KEY_ID}")
        assert response.status_code == 200
        assert response.json() == {"revoked": True, "key_id": _KEY_ID}

    @patch("api.auth.get_pool")
    def test_unknown_uuid_is_404(self, mock_get_pool):
        mock_get_pool.return_value = _pool("UPDATE 0")
        response = client.delete(f"/v1/auth/api-keys/{_KEY_ID}")
        assert response.status_code == 404
        assert response.json()["detail"] == "API key not found"

    @patch("api.auth.get_pool")
    def test_urn_prefixed_key_id_is_404_not_500(self, mock_get_pool):
        mock_get_pool.return_value = _pool("UPDATE 0")
        response = client.delete(f"/v1/auth/api-keys/urn:uuid:{_KEY_ID}")
        assert response.status_code == 404
        assert response.json()["detail"] == "API key not found"


class TestRevokeAgentApiKeyRoute:
    def setup_method(self):
        app.dependency_overrides[require_dashboard_session] = _override_session

    def teardown_method(self):
        app.dependency_overrides.pop(require_dashboard_session, None)

    @patch("api.agents.get_pool")
    def test_malformed_key_id_is_404(self, mock_get_pool):
        mock_get_pool.return_value = _pool("UPDATE 1")
        response = client.delete("/v1/agents/agent-a/api-keys/not-a-uuid")
        assert response.status_code == 404
        assert response.json()["detail"] == "API key not found"

    @patch("api.agents.get_pool")
    def test_existing_key_is_revoked(self, mock_get_pool):
        mock_get_pool.return_value = _pool("UPDATE 1")
        response = client.delete(f"/v1/agents/agent-a/api-keys/{_KEY_ID}")
        assert response.status_code == 200
        assert response.json() == {"revoked": True, "key_id": _KEY_ID, "agent": "agent-a"}
