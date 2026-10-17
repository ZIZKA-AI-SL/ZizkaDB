"""Tests for agent deletion and Qdrant vector cleanup (retried purge)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from api import agents as agents_api
from api.deps import require_dashboard_session


DASHBOARD_TENANT = {"tenant_id": "t-1", "user_id": "u-1"}


def _make_pool_conn(event_count=5):
    pool = AsyncMock()
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"event_count": event_count})
    pool.acquire = MagicMock(return_value=AsyncMock())
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


@pytest.mark.asyncio
async def test_delete_agent_purges_qdrant_vectors(monkeypatch):
    pool, _conn = _make_pool_conn()
    qdrant = AsyncMock()
    qdrant.delete = AsyncMock()

    monkeypatch.setattr("api.agents.get_pool", lambda: pool)
    monkeypatch.setattr("api.agents.get_qdrant", lambda: qdrant)

    result = await agents_api.delete_agent("my-agent", tenant=DASHBOARD_TENANT)

    assert result["deleted"] is True
    assert result["agent"] == "my-agent"
    assert result["events_deleted"] == 5
    assert result["vector_cleanup"] is True
    # Purge uses the tenant_id + agent_id payload filter (not PointIdsList,
    # not a per-event-ID fetch) and succeeds on the first attempt.
    qdrant.delete.assert_awaited_once()
    _, kwargs = qdrant.delete.call_args
    assert kwargs["collection_name"] == "agent_events"
    assert "points_selector" in kwargs


@pytest.mark.asyncio
async def test_delete_agent_purge_retries_then_succeeds(monkeypatch):
    """Transient Qdrant failure followed by success -> vector_cleanup True."""
    pool, _conn = _make_pool_conn()
    qdrant = AsyncMock()
    qdrant.delete = AsyncMock(side_effect=[RuntimeError("blip"), None])

    monkeypatch.setattr("api.agents.get_pool", lambda: pool)
    monkeypatch.setattr("api.agents.get_qdrant", lambda: qdrant)

    result = await agents_api.delete_agent("my-agent", tenant=DASHBOARD_TENANT)

    assert result["vector_cleanup"] is True
    assert qdrant.delete.call_count == 2


@pytest.mark.asyncio
async def test_delete_agent_purge_persistent_failure(monkeypatch):
    """Qdrant failing all 3 attempts -> vector_cleanup False, PG still deleted."""
    pool, _conn = _make_pool_conn()
    qdrant = AsyncMock()
    qdrant.delete = AsyncMock(side_effect=RuntimeError("Qdrant unavailable"))

    monkeypatch.setattr("api.agents.get_pool", lambda: pool)
    monkeypatch.setattr("api.agents.get_qdrant", lambda: qdrant)

    result = await agents_api.delete_agent("my-agent", tenant=DASHBOARD_TENANT)

    assert result["vector_cleanup"] is False
    assert qdrant.delete.call_count == 3
    # Postgres deletion is not gated on Qdrant: the rows were still removed.
    _conn.execute.assert_any_call(
        "DELETE FROM agents WHERE tenant_id = $1 AND agent_id = $2",
        "t-1", "my-agent",
    )


@pytest.mark.asyncio
async def test_delete_agent_not_found(monkeypatch):
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    monkeypatch.setattr("api.agents.get_pool", lambda: pool)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await agents_api.delete_agent("missing", tenant=DASHBOARD_TENANT)
    assert exc.value.status_code == 404


def test_delete_agent_requires_dashboard_session():
    import inspect

    dep = None
    for p in inspect.signature(agents_api.delete_agent).parameters.values():
        d = getattr(p.default, "dependency", None)
        if d is not None:
            dep = d
    assert dep is require_dashboard_session
