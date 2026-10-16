"""Tests for GET /v1/events/verify and GET /v1/events/{event_id}/verify."""

import datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException

from api.events import verify_event, verify_events
from services.checksum import compute_legacy_checksum, compute_row_checksum_v2

TENANT = {"tenant_id": "11111111-1111-1111-1111-111111111111"}
TS = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
EVENT_ID = "00000000-0000-0000-0000-000000000001"


def _row(**overrides):
    base = {
        "event_id": UUID(EVENT_ID),
        "tenant_id": UUID(TENANT["tenant_id"]),
        "agent_id": "agent1",
        "timestamp": TS,
        "event_type": "message",
        "data": '{"text": "hi"}',
        "parent_event_id": None,
        "session_id": None,
        "sequence_no": 3,
        "metadata": None,
    }
    base.update(overrides)
    base.setdefault("checksum", compute_row_checksum_v2(base))
    return base


@pytest.fixture
def pool(monkeypatch):
    pool = AsyncMock()
    monkeypatch.setattr("api.events.get_pool", lambda: pool)
    return pool


@pytest.mark.asyncio
async def test_verify_events_counts_statuses_and_lists_mismatches(pool, monkeypatch):
    tampered = _row(
        event_id=UUID("00000000-0000-0000-0000-000000000003"),
        checksum=compute_legacy_checksum("message", {"text": "other"}),
    )
    legacy = _row(
        event_id=UUID("00000000-0000-0000-0000-000000000002"),
        checksum=compute_legacy_checksum("message", {"text": "hi"}),
    )
    missing = _row(event_id=UUID("00000000-0000-0000-0000-000000000004"), checksum=None)
    pool.fetch = AsyncMock(return_value=[_row(), legacy, tampered, missing])
    monkeypatch.setattr("api.events.assert_agent_allowed", AsyncMock())

    result = await verify_events("agent1", limit=100, before=None, after=None, tenant=TENANT)

    assert result["summary"] == {
        "checked": 4, "valid": 1, "valid_legacy": 1, "mismatch": 1, "missing": 1,
    }
    assert [m["event_id"] for m in result["mismatches"]] == [str(tampered["event_id"])]
    assert result["oldest_timestamp"] == TS.isoformat()

    sql, *params = pool.fetch.await_args.args
    assert "tenant_id = $1" in sql and "agent_id = $2" in sql
    assert params == [TENANT["tenant_id"], "agent1", 100]


@pytest.mark.asyncio
async def test_verify_events_time_window_params(pool, monkeypatch):
    pool.fetch = AsyncMock(return_value=[])
    monkeypatch.setattr("api.events.assert_agent_allowed", AsyncMock())
    after = TS - datetime.timedelta(days=1)

    result = await verify_events("agent1", limit=5, before=TS, after=after, tenant=TENANT)

    sql, *params = pool.fetch.await_args.args
    assert "timestamp < $3" in sql and "timestamp > $4" in sql and "LIMIT $5" in sql
    assert params == [TENANT["tenant_id"], "agent1", TS, after, 5]
    assert result["summary"]["checked"] == 0
    assert result["oldest_timestamp"] is None


@pytest.mark.asyncio
async def test_verify_events_rejects_inverted_window(pool, monkeypatch):
    monkeypatch.setattr("api.events.assert_agent_allowed", AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await verify_events("agent1", limit=5, before=TS, after=TS, tenant=TENANT)
    assert exc.value.status_code == 400
    pool.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_events_requires_agent_scope(pool, monkeypatch):
    async def deny(tenant, agent):
        raise HTTPException(status_code=403, detail="agent mismatch")

    monkeypatch.setattr("api.events.assert_agent_allowed", deny)

    with pytest.raises(HTTPException) as exc:
        await verify_events("other-agent", limit=100, before=None, after=None, tenant=TENANT)

    assert exc.value.status_code == 403
    pool.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_event_single_valid(pool, monkeypatch):
    assert_allowed = AsyncMock()
    monkeypatch.setattr("api.events.assert_agent_allowed", assert_allowed)
    pool.fetchrow = AsyncMock(return_value=_row())

    result = await verify_event(EVENT_ID.upper(), tenant=TENANT)

    assert result["event_id"] == EVENT_ID
    assert result["status"] == "valid"
    assert result["version"] == "v2"
    assert pool.fetchrow.await_args.args[1:] == (UUID(EVENT_ID), TENANT["tenant_id"], None)
    assert_allowed.assert_awaited_once_with(TENANT, "agent1")


@pytest.mark.asyncio
async def test_verify_event_rejects_bad_uuid(pool):
    with pytest.raises(HTTPException) as exc:
        await verify_event("not-a-uuid", tenant=TENANT)
    assert exc.value.status_code == 404
    pool.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_event_scoped_key_filters_in_sql(pool, monkeypatch):
    """Another tenant's or another agent's event is filtered out in SQL → 404."""
    assert_allowed = AsyncMock()
    monkeypatch.setattr("api.events.assert_agent_allowed", assert_allowed)
    pool.fetchrow = AsyncMock(return_value=None)
    scoped = {**TENANT, "agent_id": "agent1"}

    with pytest.raises(HTTPException) as exc:
        await verify_event(EVENT_ID, tenant=scoped)

    assert exc.value.status_code == 404
    assert pool.fetchrow.await_args.args[3] == "agent1"
    assert_allowed.assert_not_awaited()
