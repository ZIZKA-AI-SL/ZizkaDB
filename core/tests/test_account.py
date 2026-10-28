"""Regression tests for /v1/account — delete and retention trial."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.account import require_dashboard_session
from main import app
from services.account import (
    account_options,
    delete_managed_account,
    grant_retention_trial,
)

client = TestClient(app)

_USER_ID = "22222222-2222-2222-2222-222222222222"
_TENANT_ID = "11111111-1111-1111-1111-111111111111"
_SESSION = {"tenant_id": _TENANT_ID, "user_id": _USER_ID}


def _override_session():
    return _SESSION


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _TxCtx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


def _make_delete_pool(user_row: dict | None):
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_TxCtx())
    conn.execute = AsyncMock()

    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=user_row)
    pool.acquire = MagicMock(return_value=_AcquireCtx(conn))
    return pool, conn


# ── Route tests ───────────────────────────────────────────────────────────────


class TestAccountRoutes:
    def setup_method(self):
        app.dependency_overrides[require_dashboard_session] = _override_session

    def teardown_method(self):
        app.dependency_overrides.pop(require_dashboard_session, None)

    @patch("api.account.delete_managed_account", new_callable=AsyncMock)
    def test_delete_account_success(self, mock_delete):
        response = client.delete("/v1/account")
        assert response.status_code == 200
        assert response.json() == {"message": "Account deleted"}
        mock_delete.assert_awaited_once_with(
            user_id=_USER_ID,
            tenant_id=_TENANT_ID,
        )

    @patch(
        "api.account.delete_managed_account",
        new_callable=AsyncMock,
        side_effect=PermissionError("Tenant mismatch"),
    )
    def test_delete_account_tenant_mismatch_returns_403(self, _mock_delete):
        response = client.delete("/v1/account")
        assert response.status_code == 403
        assert response.json()["detail"] == "Invalid session"

    @patch(
        "api.account.delete_managed_account",
        new_callable=AsyncMock,
        side_effect=ValueError("Account deletion is only available on managed cloud"),
    )
    def test_delete_account_not_managed_cloud_returns_400(self, _mock_delete):
        response = client.delete("/v1/account")
        assert response.status_code == 400
        assert "managed cloud" in response.json()["detail"]

    @patch("api.account.grant_retention_trial", new_callable=AsyncMock)
    def test_retention_trial_success(self, mock_grant):
        mock_grant.return_value = {
            "message": "Your trial has been extended by 30 days.",
            "trial_ends_at": "2026-10-08T00:00:00+00:00",
            "retention_trial_available": False,
        }
        response = client.post("/v1/account/retention-trial")
        assert response.status_code == 200
        assert response.json()["retention_trial_available"] is False
        mock_grant.assert_awaited_once_with(user_id=_USER_ID)

    @patch(
        "api.account.grant_retention_trial",
        new_callable=AsyncMock,
        side_effect=ValueError("You have already used your extra free month"),
    )
    def test_retention_trial_already_used_returns_400(self, _mock_grant):
        response = client.post("/v1/account/retention-trial")
        assert response.status_code == 400
        assert "already used" in response.json()["detail"]

    def test_account_options_requires_dashboard_session(self):
        app.dependency_overrides.pop(require_dashboard_session, None)
        response = client.get("/v1/account/options")
        assert response.status_code in (401, 403)


# ── Service tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_account_options_not_managed_in_development(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    result = await account_options(user_id=_USER_ID)
    assert result == {"managed_cloud": False}


@pytest.mark.asyncio
async def test_delete_managed_account_purges_data(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    pool, conn = _make_delete_pool(
        {
            "user_id": _USER_ID,
            "email": "user@example.com",
            "tenant_id": _TENANT_ID,
        }
    )
    qdrant = AsyncMock()
    qdrant.delete = AsyncMock()

    monkeypatch.setattr("services.account.get_pool", lambda: pool)
    monkeypatch.setattr("services.account.get_qdrant", lambda: qdrant)

    await delete_managed_account(user_id=_USER_ID, tenant_id=_TENANT_ID)

    qdrant.delete.assert_awaited_once()
    sql = [c.args[0] for c in conn.execute.await_args_list]
    assert len(sql) == 6
    # Lock order: tenant advisory lock, agents rows, key rows, then the deletes.
    assert "pg_advisory_xact_lock" in sql[0]
    assert "FROM agents" in sql[1] and "FOR UPDATE" in sql[1]
    assert "FROM api_keys" in sql[2] and "FOR UPDATE" in sql[2]
    assert "DELETE FROM tenants" in sql[5]
    # Vectors are purged after the transaction, once the data is gone.
    assert qdrant.delete.await_count == 1


@pytest.mark.asyncio
async def test_delete_managed_account_rejects_tenant_mismatch(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    pool, _conn = _make_delete_pool(
        {
            "user_id": _USER_ID,
            "email": "user@example.com",
            "tenant_id": "99999999-9999-9999-9999-999999999999",
        }
    )
    monkeypatch.setattr("services.account.get_pool", lambda: pool)

    with pytest.raises(PermissionError, match="Tenant mismatch"):
        await delete_managed_account(user_id=_USER_ID, tenant_id=_TENANT_ID)


@pytest.mark.asyncio
async def test_grant_retention_trial_extends_trial(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        return_value={
            "user_id": _USER_ID,
            "email": "user@example.com",
            "retention_trial_used": False,
            "trial_ends_at": None,
        }
    )
    pool.execute = AsyncMock()
    monkeypatch.setattr("services.account.get_pool", lambda: pool)

    result = await grant_retention_trial(user_id=_USER_ID)

    assert "extended" in result["message"]
    assert result["retention_trial_available"] is False
    pool.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_grant_retention_trial_rejects_second_use(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        return_value={
            "user_id": _USER_ID,
            "email": "user@example.com",
            "retention_trial_used": True,
            "trial_ends_at": None,
        }
    )
    monkeypatch.setattr("services.account.get_pool", lambda: pool)

    with pytest.raises(ValueError, match="already used"):
        await grant_retention_trial(user_id=_USER_ID)
