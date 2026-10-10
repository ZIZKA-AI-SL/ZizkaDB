"""Tests for GET /v1/events/verify and GET /v1/events/{event_id}/verify."""

import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.events import verify_events, verify_event
from services.checksum import compute_event_checksum_v2, compute_legacy_checksum

TENANT = {"tenant_id": "t-1"}
TS = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)


def _row(**overrides):
    base = {
        "event_id": "00000000-0000-0000-0000-000000000001",
        "tenant_id": "t-1",
        "agent_id": "agent1",
        "timestamp": TS,
        "event_type": "message",
        "data": {"text": "hi"},
        "parent_event_id": None,
        "session_id": None,
        "sequence_no": 3,
        "metadata": None,
    }
    base.update(overrides)
    if "checksum" not in overrides:
        base["checksum"] = compute_event_checksum_v2(
            tenant_id=base["tenant_id"],
            agent_id=base["agent_id"],
            event_type=base["event_type"],
            data=base["data"],
            parent_event_id=base["parent_event_id"],
            session_id=base["session_id"],
            timestamp=base["timestamp"],
            sequence_no=base["sequence_no"],
            metadata=base["metadata"],
        )
    return base


@pytest.mark.asyncio
async def test_verify_events_counts_statuses_and_lists_mismatches(monkeypatch):
    pool = AsyncMock()
    tampered = _row(checksum=compute_legacy_checksum("message", {"text": "other"}))
    legacy = _row(
        event_id="00000000-0000-0000-0000-000000000002",
        checksum=compute_legacy_checksum("message", {"text": "hi"}),
    )
    pool.fetch = AsyncMock(return_value=[_row(), legacy, tampered])
    monkeypatch.setattr("api.events.get_pool", lambda: pool)
    monkeypatch.setattr("api.events.assert_agent_allowed", AsyncMock())

    result = await verify_events("agent1", tenant=TENANT)

    assert result["summary"] == {
        "checked": 3,
        "valid": 1,
        "valid_legacy": 1,
        "mismatch": 1,
        "missing": 0,
    }
    assert len(result["mismatches"]) == 1
    assert result["mismatches"][0]["event_id"] == tampered["event_id"]

    sql = pool.fetch.await_args.args[0]
    assert "tenant_id = $1" in sql
    assert "agent_id = $2" in sql
    assert "checksum" in sql


@pytest.mark.asyncio
async def test_verify_events_requires_agent_scope(monkeypatch):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    monkeypatch.setattr("api.events.get_pool", lambda: pool)

    async def deny(tenant, agent):
        raise HTTPException(status_code=403, detail="agent mismatch")

    monkeypatch.setattr("api.events.assert_agent_allowed", deny)

    with pytest.raises(HTTPException) as exc:
        await verify_events("other-agent", tenant=TENANT)

    assert exc.value.status_code == 403
    pool.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_event_single_valid(monkeypatch):
    pool = AsyncMock()
    row = _row()
    pool.fetchrow = AsyncMock(return_value=row)
    monkeypatch.setattr("api.events.get_pool", lambda: pool)

    result = await verify_event(row["event_id"], tenant=TENANT)

    assert result["event_id"] == row["event_id"]
    assert result["status"] == "valid"
    assert result["version"] == "v2"
    assert pool.fetchrow.await_args.args[1:] == (row["event_id"], "t-1")


@pytest.mark.asyncio
async def test_verify_event_rejects_bad_uuid(monkeypatch):
    pool = AsyncMock()
    monkeypatch.setattr("api.events.get_pool", lambda: pool)

    with pytest.raises(HTTPException) as exc:
        await verify_event("not-a-uuid", tenant=TENANT)

    assert exc.value.status_code == 404
    pool.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_event_tenant_scoping(monkeypatch):
    pool = AsyncMock()
    monkeypatch.setattr("api.events.get_pool", lambda: pool)

    # Row belongs to another tenant → 404, nothing leaks.
    pool.fetchrow = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await verify_event("00000000-0000-0000-0000-000000000001", tenant=TENANT)
    assert exc.value.status_code == 404

    # Scoped agent key must not verify another agent's event → 404.
    pool.fetchrow = AsyncMock(return_value=_row(agent_id="agent2"))
    scoped = {**TENANT, "agent_id": "agent1"}
    with pytest.raises(HTTPException) as exc:
        await verify_event("00000000-0000-0000-0000-000000000001", tenant=scoped)
    assert exc.value.status_code == 404
