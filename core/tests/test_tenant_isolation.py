"""Regression tests for cross-tenant leaks via parent_event_id (write_event validation + baseline transitions query)."""

from unittest.mock import AsyncMock, MagicMock
import datetime

import pytest
from fastapi import HTTPException

from services.event_write import write_event
from api.agents import _baseline_for, _baseline_for_timewindow


TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
PARENT_EVENT_ID = "33333333-3333-3333-3333-333333333333"


@pytest.fixture
def mock_pool(monkeypatch):
    pool = AsyncMock()
    # write_event runs the parent check, agent upsert and event insert on one
    # connection inside a transaction; expose that conn as ``pool.conn``.
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=AsyncMock())
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.conn = conn
    monkeypatch.setattr("services.event_write.get_pool", lambda: pool)
    monkeypatch.setattr("services.event_write.get_qdrant", lambda: AsyncMock())
    monkeypatch.setattr(
        "services.event_write.generate_embedding", AsyncMock(return_value=None)
    )
    return pool


@pytest.mark.asyncio
async def test_write_event_rejects_cross_tenant_parent(mock_pool):
    """Tenant B must not be able to link a new event to tenant A's event_id."""
    mock_pool.conn.fetchval.return_value = TENANT_A  # parent belongs to a different tenant

    with pytest.raises(HTTPException) as exc_info:
        await write_event(
            tenant_id=TENANT_B,
            agent="agent-b",
            event="child_event",
            data={},
            parent_id=PARENT_EVENT_ID,
        )

    assert exc_info.value.status_code == 400
    mock_pool.conn.execute.assert_not_called()  # never reaches the INSERT


@pytest.mark.asyncio
async def test_write_event_rejects_nonexistent_parent(mock_pool):
    mock_pool.conn.fetchval.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        await write_event(
            tenant_id=TENANT_B,
            agent="agent-b",
            event="child_event",
            data={},
            parent_id=PARENT_EVENT_ID,
        )

    assert exc_info.value.status_code == 400
    mock_pool.conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_write_event_allows_same_tenant_parent(mock_pool):
    mock_pool.conn.fetchval.return_value = TENANT_A
    mock_pool.conn.fetchrow.return_value = {
        "event_id": "44444444-4444-4444-4444-444444444444",
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "sequence_no": 2,
    }

    result = await write_event(
        tenant_id=TENANT_A,
        agent="agent-a",
        event="child_event",
        data={},
        parent_id=PARENT_EVENT_ID,
    )

    assert result["event_id"] == "44444444-4444-4444-4444-444444444444"


def _transition_sql(pool):
    """Pull the SQL text of the transitions query (the one joining events p)."""
    calls = [c for c in pool.fetch.await_args_list if "JOIN events p" in c.args[0]]
    assert calls, "transitions query was not executed"
    return calls[0].args[0]


@pytest.mark.asyncio
async def test_baseline_transitions_query_scopes_parent_by_tenant():
    """The event_type -> event_type transition query must not join across tenants."""
    pool = AsyncMock()
    pool.fetch.return_value = []
    pool.fetchrow.return_value = {"avg_events": 0, "avg_dur": 0, "pct": 0}

    await _baseline_for(pool, TENANT_A, "agent-a", ["session-1"])

    sql = _transition_sql(pool)
    assert "p.tenant_id" in sql


@pytest.mark.asyncio
async def test_behavior_change_transitions_query_scopes_parent_by_tenant():
    pool = AsyncMock()
    pool.fetch.return_value = []
    pool.fetchrow.return_value = {"pct": 0}

    now = datetime.datetime(2026, 1, 1)
    await _baseline_for_timewindow(pool, TENANT_A, "agent-a", None, now)

    sql = _transition_sql(pool)
    assert "p.tenant_id" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("from_ts", [None, datetime.datetime(2025, 12, 1)])
async def test_behavior_change_transitions_query_prefixes_timestamp_once(from_ts):
    # Regression: the window filter rendered as "c.c.timestamp", a Postgres
    # error (500) once an agent had enough history for a baseline.
    pool = AsyncMock()
    pool.fetch.return_value = []
    pool.fetchrow.return_value = {"pct": 0}

    await _baseline_for_timewindow(pool, TENANT_A, "agent-a", from_ts, datetime.datetime(2026, 1, 1))

    sql = _transition_sql(pool)
    assert "c.c." not in sql
    assert "c.timestamp <" in sql
