"""Tests for agent deletion and Qdrant vector cleanup."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from api import agents as agents_api
from api.deps import require_dashboard_session
from services.exceptions import service_unavailable


DASHBOARD_TENANT = {"tenant_id": "t-1", "user_id": "u-1"}


def _make_event_row(event_id: str) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, key: event_id if key == "event_id" else None
    return row


@pytest.mark.asyncio
async def test_delete_agent_tombstones_events(monkeypatch):
    pool = AsyncMock()
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=1)  # agent exists
    conn.fetch = AsyncMock(
        return_value=[_make_event_row("evt-1"), _make_event_row("evt-2")]
    )
    conn.execute = AsyncMock()
    pool.acquire = MagicMock(return_value=AsyncMock())
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    qdrant = AsyncMock()
    qdrant.delete = AsyncMock()

    monkeypatch.setattr("api.agents.get_pool", lambda: pool)
    monkeypatch.setattr("api.agents.get_qdrant", lambda: qdrant)
    monkeypatch.setattr("api.agents.embeddings_enabled", lambda: True)

    result = await agents_api.delete_agent("my-agent", tenant=DASHBOARD_TENANT)

    # The agent row is locked before anything is deleted.
    assert "FOR UPDATE" in conn.fetchval.await_args.args[0]
    assert result["deleted"] is True
    assert result["events_deleted"] == 2  # two events tombstoned
    assert result["agent"] == "my-agent"
    qdrant.delete.assert_awaited_once()
    _, kwargs = qdrant.delete.call_args
    assert kwargs["collection_name"] == "agent_events"
    # delete_and_tombstone was called
    assert conn.execute.await_count >= 3  # tombstones + agent row


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


@pytest.mark.asyncio
async def test_delete_agent_qdrant_unavailable_raises(monkeypatch):
    pool = AsyncMock()
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=1)  # agent exists
    conn.fetch = AsyncMock(return_value=[_make_event_row("evt-1")])
    conn.execute = AsyncMock()
    pool.acquire = MagicMock(return_value=AsyncMock())
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    qdrant = AsyncMock()
    qdrant.delete = AsyncMock(side_effect=Exception("Qdrant down"))

    monkeypatch.setattr("api.agents.get_pool", lambda: pool)
    monkeypatch.setattr("api.agents.get_qdrant", lambda: qdrant)
    monkeypatch.setattr("api.agents.embeddings_enabled", lambda: True)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await agents_api.delete_agent("my-agent", tenant=DASHBOARD_TENANT)
    assert exc.value.status_code == 503
    # Nothing should have been deleted (transaction rolled back)
    conn.execute.assert_not_awaited()
