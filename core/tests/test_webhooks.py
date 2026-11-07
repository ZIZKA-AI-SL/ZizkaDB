"""Tests for webhook API routes and service functions."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from api import webhooks as webhooks_api
from api.deps import require_dashboard_session
from services.webhooks import (
    create_subscription,
    delete_subscription,
    get_subscription,
    list_subscriptions,
    update_subscription,
)

DASHBOARD_TENANT = {"tenant_id": "t-1", "user_id": "u-1"}


def _make_pool_conn():
    """Create a mock pool/conn pair for database access."""
    pool = AsyncMock()
    conn = AsyncMock()

    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=None)

    pool.acquire = MagicMock(return_value=AsyncMock())
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    pool.fetchrow = AsyncMock(return_value={})
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock(return_value="OK")

    return pool, conn


def _patch_pool(monkeypatch, pool):
    monkeypatch.setattr("services.webhooks.get_pool", lambda: pool)


@pytest.mark.asyncio
async def test_list_subscriptions(monkeypatch):
    pool, conn = _make_pool_conn()
    pool.fetch = AsyncMock(
        return_value=[
            {
                "webhook_id": "00000000-0000-0000-0000-000000000001",
                "tenant_id": "t-1",
                "url": "https://example.com/webhook",
                "secret": "secret123",
                "agent_id": None,
                "event_types": ["tool_call", "user_message"],
                "active": True,
                "created_at": "2026-01-01T00:00:00Z",
                "last_triggered": None,
                "last_status": None,
                "last_body": None,
                "failure_count": 0,
            }
        ]
    )
    _patch_pool(monkeypatch, pool)

    rows = await list_subscriptions("t-1")

    assert len(rows) == 1
    assert rows[0]["webhook_id"] == "00000000-0000-0000-0000-000000000001"
    assert rows[0]["url"] == "https://example.com/webhook"
    assert rows[0]["secret"] == "secret123"
    assert rows[0]["agent_id"] is None
    assert rows[0]["event_types"] == ["tool_call", "user_message"]
    assert rows[0]["active"] is True


@pytest.mark.asyncio
async def test_create_subscription(monkeypatch):
    pool, conn = _make_pool_conn()
    pool.fetchrow = AsyncMock(
        return_value={
            "webhook_id": "00000000-0000-0000-0000-000000000001",
            "tenant_id": "t-1",
            "url": "https://example.com/webhook",
            "secret": "secret123",
            "agent_id": None,
            "event_types": ["tool_call"],
            "active": True,
            "created_at": "2026-01-01T00:00:00Z",
            "last_triggered": None,
            "last_status": None,
            "last_body": None,
            "failure_count": 0,
        }
    )
    _patch_pool(monkeypatch, pool)

    row = await create_subscription(
        tenant_id="t-1",
        url="https://example.com/webhook",
        secret="secret123",
        agent_id=None,
        event_types=["tool_call"],
    )

    assert row["webhook_id"] == "00000000-0000-0000-0000-000000000001"
    assert row["url"] == "https://example.com/webhook"
    assert row["secret"] == "secret123"
    assert row["agent_id"] is None
    assert row["event_types"] == ["tool_call"]
    assert row["active"] is True


@pytest.mark.asyncio
async def test_create_subscription_unsafe_url(monkeypatch):
    pool, conn = _make_pool_conn()
    _patch_pool(monkeypatch, pool)

    with pytest.raises(ValueError, match="Unsafe webhook URL"):
        await create_subscription(
            tenant_id="t-1",
            url="http://169.254.169.254/metadata",
            secret=None,
            agent_id=None,
            event_types=None,
        )


@pytest.mark.asyncio
async def test_get_subscription(monkeypatch):
    pool, conn = _make_pool_conn()
    pool.fetchrow = AsyncMock(
        return_value={
            "webhook_id": "00000000-0000-0000-0000-000000000001",
            "tenant_id": "t-1",
            "url": "https://example.com/webhook",
            "secret": "secret123",
            "agent_id": None,
            "event_types": ["tool_call"],
            "active": True,
            "created_at": "2026-01-01T00:00:00Z",
            "last_triggered": None,
            "last_status": None,
            "last_body": None,
            "failure_count": 0,
        }
    )
    _patch_pool(monkeypatch, pool)

    result = await get_subscription(
        "00000000-0000-0000-0000-000000000001", "t-1"
    )

    assert result is not None
    assert result["webhook_id"] == "00000000-0000-0000-0000-000000000001"
    assert result["url"] == "https://example.com/webhook"
    assert result["secret"] == "secret123"


@pytest.mark.asyncio
async def test_get_subscription_not_found(monkeypatch):
    pool, conn = _make_pool_conn()
    pool.fetchrow = AsyncMock(return_value=None)
    _patch_pool(monkeypatch, pool)

    result = await get_subscription(
        "00000000-0000-0000-0000-000000000001", "t-1"
    )

    assert result is None


@pytest.mark.asyncio
async def test_update_subscription(monkeypatch):
    pool, conn = _make_pool_conn()
    pool.fetchrow = AsyncMock(
        return_value={
            "webhook_id": "00000000-0000-0000-0000-000000000001",
            "tenant_id": "t-1",
            "url": "https://example.com/webhook2",
            "secret": "newsecret123",
            "agent_id": "my-agent",
            "event_types": ["tool_call", "user_message"],
            "active": True,
            "created_at": "2026-01-01T00:00:00Z",
            "last_triggered": None,
            "last_status": None,
            "last_body": None,
            "failure_count": 0,
        }
    )
    _patch_pool(monkeypatch, pool)

    result = await update_subscription(
        webhook_id="00000000-0000-0000-0000-000000000001",
        tenant_id="t-1",
        url="https://example.com/webhook2",
        secret="newsecret123",
        agent_id="my-agent",
        event_types=["tool_call", "user_message"],
        active=True,
    )

    assert result is not None
    assert result["webhook_id"] == "00000000-0000-0000-0000-000000000001"
    assert result["url"] == "https://example.com/webhook2"
    assert result["secret"] == "newsecret123"
    assert result["agent_id"] == "my-agent"
    assert result["event_types"] == ["tool_call", "user_message"]
    assert result["active"] is True


@pytest.mark.asyncio
async def test_delete_subscription(monkeypatch):
    pool, conn = _make_pool_conn()
    pool.execute = AsyncMock(return_value="DELETE 1")
    _patch_pool(monkeypatch, pool)

    result = await delete_subscription(
        webhook_id="00000000-0000-0000-0000-000000000001",
        tenant_id="t-1",
    )

    assert result is True


@pytest.mark.asyncio
async def test_delete_subscription_not_found(monkeypatch):
    pool, conn = _make_pool_conn()
    pool.execute = AsyncMock(return_value="DELETE 0")
    _patch_pool(monkeypatch, pool)

    result = await delete_subscription(
        webhook_id="00000000-0000-0000-0000-000000000001",
        tenant_id="t-1",
    )

    assert result is False


@pytest.mark.asyncio
async def test_webhook_test_endpoint_success(monkeypatch):
    pool, conn = _make_pool_conn()
    pool.fetchrow = AsyncMock(
        return_value={
            "webhook_id": "00000000-0000-0000-0000-000000000001",
            "tenant_id": "t-1",
            "url": "https://example.com/webhook",
            "secret": "secret123",
            "agent_id": None,
            "event_types": ["tool_call"],
            "active": True,
            "created_at": "2026-01-01T00:00:00Z",
            "last_triggered": None,
            "last_status": None,
            "last_body": None,
            "failure_count": 0,
        }
    )
    pool.execute = AsyncMock(return_value="UPDATE 1")

    # Patch both get_pool locations (services.webhooks and api.webhooks)
    monkeypatch.setattr("services.webhooks.get_pool", lambda: pool)
    monkeypatch.setattr("api.webhooks.get_pool", lambda: pool)

    # Mock _deliver_one to return success
    async def fake_deliver_one(sub, event):
        return True, 200, '{"status": "ok"}'

    monkeypatch.setattr("api.webhooks._deliver_one", fake_deliver_one)

    result = await webhooks_api.test_webhook(
        webhook_id="00000000-0000-0000-0000-000000000001",
        session=DASHBOARD_TENANT,
    )

    assert result == {"delivered": True, "webhook_id": "00000000-0000-0000-0000-000000000001"}


@pytest.mark.asyncio
async def test_webhook_test_endpoint_failure(monkeypatch):
    pool, conn = _make_pool_conn()
    pool.fetchrow = AsyncMock(
        return_value={
            "webhook_id": "00000000-0000-0000-0000-000000000001",
            "tenant_id": "t-1",
            "url": "https://example.com/webhook",
            "secret": "secret123",
            "agent_id": None,
            "event_types": ["tool_call"],
            "active": True,
            "created_at": "2026-01-01T00:00:00Z",
            "last_triggered": None,
            "last_status": None,
            "last_body": None,
            "failure_count": 0,
        }
    )
    pool.execute = AsyncMock(return_value="UPDATE 1")

    monkeypatch.setattr("services.webhooks.get_pool", lambda: pool)
    monkeypatch.setattr("api.webhooks.get_pool", lambda: pool)

    async def fake_deliver_one(sub, event):
        return False, None, None

    monkeypatch.setattr("api.webhooks._deliver_one", fake_deliver_one)

    result = await webhooks_api.test_webhook(
        webhook_id="00000000-0000-0000-0000-000000000001",
        session=DASHBOARD_TENANT,
    )

    assert result == {"delivered": False, "webhook_id": "00000000-0000-0000-0000-000000000001"}
