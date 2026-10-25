"""Endpoint-level tests for API-key plan-limit enforcement.

Complements test_entitlements.py (which exercises the guard directly against a
fake connection) by hitting the actual FastAPI routes through TestClient, so a
regression in how the routes wire up the guard/usage endpoint would be caught
here even if the guard's own unit tests still pass.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app
from api.deps import require_dashboard_session

client = TestClient(app)

_SESSION = {"tenant_id": "11111111-1111-1111-1111-111111111111", "user_id": "22222222-2222-2222-2222-222222222222"}


def _override_session():
    return _SESSION


class _NullCtx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakeConn:
    """Routes fetchval by SQL substring, same convention as test_entitlements.py."""

    def __init__(self, plan: str | None, count: int):
        self._plan = plan
        self._count = count

    def transaction(self):
        return _NullCtx()

    async def execute(self, query, *args):
        return "SELECT 1"

    async def fetchval(self, query, *args):
        if "COUNT(" in query:
            return self._count
        return self._plan

    async def fetchrow(self, query, *args):
        return {"key_id": "33333333-3333-3333-3333-333333333333", "expires_at": None}


class FakePool:
    def __init__(self, conn: FakeConn):
        self._conn = conn

    def acquire(self):
        return _AcquireCtx(self._conn)

    async def fetchval(self, query, *args):
        return await self._conn.fetchval(query, *args)


class TestCreateApiKeyEndpoint:
    def setup_method(self):
        app.dependency_overrides[require_dashboard_session] = _override_session

    def teardown_method(self):
        app.dependency_overrides.pop(require_dashboard_session, None)

    @patch.dict("os.environ", {"API_KEY_LIMITS_ENFORCED": "true"})
    @patch("api.auth.get_pool")
    def test_blocked_at_limit(self, mock_get_pool):
        mock_get_pool.return_value = FakePool(FakeConn(plan="pro", count=2))  # limit 2, full
        response = client.post("/v1/auth/api-keys", json={"name": "prod key"})
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "api_key_limit_reached"
        assert response.json()["detail"]["limit"] == 2

    @patch.dict("os.environ", {"API_KEY_LIMITS_ENFORCED": "true"})
    @patch("api.auth.get_pool")
    def test_allowed_under_limit(self, mock_get_pool):
        mock_get_pool.return_value = FakePool(FakeConn(plan="pro", count=1))  # limit 2, one free slot
        response = client.post("/v1/auth/api-keys", json={"name": "prod key"})
        assert response.status_code == 200
        assert "key" in response.json()

    @patch.dict("os.environ", {"API_KEY_LIMITS_ENFORCED": "false"})
    @patch("api.auth.get_pool")
    def test_not_blocked_when_flag_disabled(self, mock_get_pool):
        # Wildly over any real limit, but enforcement is off.
        mock_get_pool.return_value = FakePool(FakeConn(plan="pro", count=999))
        response = client.post("/v1/auth/api-keys", json={"name": "prod key"})
        assert response.status_code == 200

    def test_requires_dashboard_session(self):
        # No dependency override here — exercise the real auth dependency.
        app.dependency_overrides.pop(require_dashboard_session, None)
        response = client.post("/v1/auth/api-keys", json={"name": "prod key"})
        assert response.status_code in (401, 403)


class TestApiKeyUsageEndpoint:
    def setup_method(self):
        app.dependency_overrides[require_dashboard_session] = _override_session

    def teardown_method(self):
        app.dependency_overrides.pop(require_dashboard_session, None)

    @patch.dict("os.environ", {"API_KEY_LIMITS_ENFORCED": "true"})
    @patch("api.auth.get_pool")
    def test_reports_plan_limit_and_usage(self, mock_get_pool):
        mock_get_pool.return_value = FakePool(FakeConn(plan="team", count=3))
        response = client.get("/v1/auth/api-keys/usage")
        assert response.status_code == 200
        body = response.json()
        assert body == {"plan": "team", "limit": 5, "used": 3, "unlimited": False, "at_limit": False}

    @patch.dict("os.environ", {"API_KEY_LIMITS_ENFORCED": "false"})
    @patch("api.auth.get_pool")
    def test_reports_unlimited_when_flag_disabled(self, mock_get_pool):
        mock_get_pool.return_value = FakePool(FakeConn(plan="pro", count=999))
        response = client.get("/v1/auth/api-keys/usage")
        assert response.status_code == 200
        body = response.json()
        assert body["unlimited"] is True
        assert body["at_limit"] is False
