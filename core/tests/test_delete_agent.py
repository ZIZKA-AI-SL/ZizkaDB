"""Tests for agent deletion and Qdrant vector cleanup."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from api import agents as agents_api
from api.deps import require_dashboard_session


DASHBOARD_TENANT = {"tenant_id": "t-1", "user_id": "u-1"}


@pytest.mark.asyncio
async def test_delete_agent_purges_qdrant_vectors(monkeypatch):
    pool = AsyncMock()
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="DELETE 5")
    conn.fetchval = AsyncMock(return_value=1)
    pool.acquire = MagicMock(return_value=AsyncMock())
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    qdrant = AsyncMock()
    qdrant.delete = AsyncMock()

    monkeypatch.setattr("api.agents.get_pool", lambda: pool)
    monkeypatch.setattr("api.agents.get_qdrant", lambda: qdrant)

    result = await agents_api.delete_agent("my-agent", tenant=DASHBOARD_TENANT)

    # The agent row is locked before anything is deleted.
    assert "FOR UPDATE" in conn.fetchval.await_args.args[0]
    assert result["deleted"] is True
    assert result["events_deleted"] == 5  # from the DELETE's row count
    assert result["agent"] == "my-agent"
    qdrant.delete.assert_awaited_once()
    _, kwargs = qdrant.delete.call_args
    assert kwargs["collection_name"] == "agent_events"


@pytest.mark.asyncio
async def test_delete_agent_not_found(monkeypatch):
    pool = AsyncMock()
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.fetchval = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=AsyncMock())
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    monkeypatch.setattr("api.agents.get_pool", lambda: pool)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await agents_api.delete_agent("missing", tenant=DASHBOARD_TENANT)
    assert exc.value.status_code == 404
    conn.execute.assert_not_awaited()


def test_delete_agent_requires_dashboard_session():
    import inspect

    dep = None
    for p in inspect.signature(agents_api.delete_agent).parameters.values():
        d = getattr(p.default, "dependency", None)
        if d is not None:
            dep = d
    assert dep is require_dashboard_session
