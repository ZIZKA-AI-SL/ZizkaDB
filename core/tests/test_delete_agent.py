"""Tests for agent deletion and Qdrant vector cleanup (retried purge)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from api import agents as agents_api
from api.deps import require_dashboard_session


DASHBOARD_TENANT = {"tenant_id": "t-1", "user_id": "u-1"}


def _make_pool_conn(order=None):
    """A pool whose one connection runs main's delete transaction. When
    ``order`` is a list, the transaction appends "commit" to it on exit."""
    pool = AsyncMock()
    conn = AsyncMock()

    async def _commit(*_exc):
        if order is not None:
            order.append("commit")
        return False

    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(side_effect=_commit)
    conn.execute = AsyncMock(return_value="DELETE 5")
    conn.fetchval = AsyncMock(return_value=1)
    pool.acquire = MagicMock(return_value=AsyncMock())
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


def _patch(monkeypatch, pool, qdrant):
    monkeypatch.setattr("api.agents.get_pool", lambda: pool)
    monkeypatch.setattr("api.agents.get_qdrant", lambda: qdrant)
    sleep = AsyncMock()
    monkeypatch.setattr("api.agents.asyncio.sleep", sleep)
    return sleep


@pytest.mark.asyncio
async def test_delete_agent_purges_qdrant_vectors(monkeypatch):
    pool, conn = _make_pool_conn()
    qdrant = AsyncMock()
    sleep = _patch(monkeypatch, pool, qdrant)

    result = await agents_api.delete_agent("my-agent", tenant=DASHBOARD_TENANT)

    # The agent row is locked before anything is deleted.
    assert "FOR UPDATE" in conn.fetchval.await_args.args[0]
    assert result["deleted"] is True
    assert result["events_deleted"] == 5  # from the DELETE's row count
    assert result["agent"] == "my-agent"
    assert result["vector_cleanup"] is True
    qdrant.delete.assert_awaited_once()
    sleep.assert_not_awaited()
    _, kwargs = qdrant.delete.call_args
    assert kwargs["collection_name"] == "agent_events"
    # One constant-size payload filter on tenant + agent, never a list of ids.
    conditions = {
        c.key: c.match.value for c in kwargs["points_selector"].filter.must
    }
    assert conditions == {"tenant_id": "t-1", "agent_id": "my-agent"}


@pytest.mark.asyncio
async def test_delete_agent_purges_after_commit(monkeypatch):
    # Qdrant is called only once the transaction has committed, so no row
    # lock is held while it retries and a 404 never reaches Qdrant.
    order = []
    pool, _ = _make_pool_conn(order)
    qdrant = AsyncMock()
    qdrant.delete.side_effect = lambda **_kw: order.append("qdrant")
    _patch(monkeypatch, pool, qdrant)

    await agents_api.delete_agent("my-agent", tenant=DASHBOARD_TENANT)

    assert order == ["commit", "qdrant"]


@pytest.mark.asyncio
async def test_delete_agent_purge_retries_then_succeeds(monkeypatch):
    pool, _ = _make_pool_conn()
    qdrant = AsyncMock()
    qdrant.delete.side_effect = [RuntimeError("blip"), None]
    sleep = _patch(monkeypatch, pool, qdrant)

    result = await agents_api.delete_agent("my-agent", tenant=DASHBOARD_TENANT)

    assert result["vector_cleanup"] is True
    assert qdrant.delete.await_count == 2
    assert [c.args[0] for c in sleep.await_args_list] == [0.5]


@pytest.mark.asyncio
async def test_delete_agent_purge_persistent_failure(monkeypatch, caplog):
    pool, conn = _make_pool_conn()
    qdrant = AsyncMock()
    qdrant.delete.side_effect = RuntimeError("Qdrant unavailable")
    sleep = _patch(monkeypatch, pool, qdrant)

    result = await agents_api.delete_agent("my-agent", tenant=DASHBOARD_TENANT)

    assert result["deleted"] is True
    assert result["vector_cleanup"] is False
    assert qdrant.delete.await_count == 3
    # Backs off between attempts, not after the last one.
    assert [c.args[0] for c in sleep.await_args_list] == [0.5, 1.0]
    assert caplog.text.count("Qdrant purge attempt") == 3
    # Postgres deletion is not gated on Qdrant: the rows were still removed.
    conn.execute.assert_any_await(
        "DELETE FROM agents WHERE tenant_id = $1 AND agent_id = $2",
        "t-1", "my-agent",
    )


@pytest.mark.asyncio
async def test_delete_agent_not_found(monkeypatch):
    pool = AsyncMock()
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.fetchval = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=AsyncMock())
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    qdrant = AsyncMock()
    monkeypatch.setattr("api.agents.get_pool", lambda: pool)
    monkeypatch.setattr("api.agents.get_qdrant", lambda: qdrant)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await agents_api.delete_agent("missing", tenant=DASHBOARD_TENANT)
    assert exc.value.status_code == 404
    conn.execute.assert_not_awaited()
    qdrant.delete.assert_not_awaited()


def test_delete_agent_requires_dashboard_session():
    import inspect

    dep = None
    for p in inspect.signature(agents_api.delete_agent).parameters.values():
        d = getattr(p.default, "dependency", None)
        if d is not None:
            dep = d
    assert dep is require_dashboard_session
