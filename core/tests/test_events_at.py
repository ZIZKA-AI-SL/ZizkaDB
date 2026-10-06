"""Tests for GET /v1/events/at (time travel)."""

import asyncio
import datetime
import json
import os
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.events import time_travel


FULL_TENANT = {"tenant_id": "t-1"}
TS = datetime.datetime(2024, 6, 1, tzinfo=datetime.timezone.utc)
LAST_ID = "00000000-0000-0000-0000-000000000009"


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


def _make_pool(rows: list[dict], event_count: int):
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_TxCtx())
    conn.fetchval = AsyncMock(return_value=event_count)
    conn.fetch = AsyncMock(return_value=rows)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AcquireCtx(conn))
    return pool, conn


def _state_row(key: str, value) -> dict:
    """A key whose newest write is a STATE_SET, with that value as JSON text."""
    return {"key": key, "value_json": json.dumps(value), "event_id": None,
            "event_type": None, "timestamp": None, "event_json": None}


def _last_event_row(event_type: str, data: dict) -> dict:
    """The newest event of any other type, which becomes state["_last_event"]."""
    return {"key": "_last_event", "value_json": None, "event_id": uuid.UUID(LAST_ID),
            "event_type": event_type, "timestamp": TS, "event_json": json.dumps(data)}


@pytest.mark.asyncio
async def test_events_at_builds_state_from_the_newest_write_per_key(monkeypatch):
    rows = [
        _last_event_row("tool_call", {"tool": "refund"}),
        _state_row("plan", "pro"),
        _state_row("profile", {"tier": None, "tags": ["a"]}),
        _state_row("step", 7),
        _state_row("title", None),
    ]
    pool, _ = _make_pool(rows, event_count=20_001)
    monkeypatch.setattr("api.events.get_pool", lambda: pool)
    monkeypatch.setattr("api.events.assert_agent_allowed", AsyncMock())

    result = await time_travel("agent1", TS, tenant=FULL_TENANT)

    assert result["state"] == {
        "_last_event": {
            "event_id": LAST_ID,
            "type": "tool_call",
            "timestamp": TS.isoformat(),
            "data": {"tool": "refund"},
        },
        "plan": "pro",
        "profile": {"tier": None, "tags": ["a"]},
        "step": 7,
        "title": None,
    }
    assert result["event_count"] == 20_001
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_events_at_reads_count_and_state_from_one_snapshot_without_a_row_cap(monkeypatch):
    pool, conn = _make_pool([], event_count=0)
    monkeypatch.setattr("api.events.get_pool", lambda: pool)
    monkeypatch.setattr("api.events.assert_agent_allowed", AsyncMock())

    result = await time_travel("agent1", TS, tenant=FULL_TENANT)

    assert result["state"] == {}
    assert result["event_count"] == 0
    conn.transaction.assert_called_once_with(isolation="repeatable_read", readonly=True)
    # The state query takes tenant, agent and time only: no limit on how much history counts.
    assert conn.fetch.await_args.args[1:] == ("t-1", "agent1", TS)
    assert conn.fetchval.await_args.args[1:] == ("t-1", "agent1", TS)


# ── Live stack (ZIZKADB_RUN_INTEGRATION=1) ────────────────────────────────────

LIVE_BASE = os.getenv("ZIZKADB_TEST_URL", "http://localhost:8000")
LIVE_KEY = os.getenv("DEV_API_KEY", "zizkadb_dev_local")
LIVE_CONCURRENCY = 20


def _live_client():
    import httpx

    # Keep every concurrent connection alive. A request beyond the kept-alive pool opens
    # and closes its own socket, and over 10,000 writes the sockets left in TIME_WAIT can
    # exhaust local ports for the tests that run next.
    return httpx.AsyncClient(
        base_url=LIVE_BASE,
        timeout=30,
        headers={"Authorization": f"Bearer {LIVE_KEY}"},
        limits=httpx.Limits(
            max_connections=LIVE_CONCURRENCY, max_keepalive_connections=LIVE_CONCURRENCY
        ),
    )


async def _log(client, agent: str, event: str, data: dict) -> dict:
    res = await client.post("/v1/events", json={"agent": agent, "event": event, "data": data})
    assert res.status_code == 201, res.text
    return res.json()


async def _state_after_everything(client, agent: str):
    at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)
    return await client.get("/v1/events/at", params={"agent": agent, "timestamp": at.isoformat()})


@pytest.mark.asyncio
@pytest.mark.integration
async def test_events_at_is_exact_beyond_ten_thousand_events():
    """Regression: the state used to be rebuilt from the agent's oldest 10,000 events only.

    10,000 events sit between an early and a late state, so the old reconstruction stopped
    before the late writes and returned plan "free" and a deleted "temp" at a time when
    the agent had plan "pro".
    """
    agent = f"time-travel-{uuid.uuid4().hex[:12]}"
    async with _live_client() as client:
        await _log(client, agent, "STATE_SET", {"plan": "free", "temp": "draft"})
        gate = asyncio.Semaphore(LIVE_CONCURRENCY)

        async def filler(i: int) -> None:
            async with gate:
                await _log(client, agent, "tool_call", {"i": i})

        await asyncio.gather(*(filler(i) for i in range(10_000)))
        await _log(client, agent, "STATE_SET", {"plan": "pro"})
        await _log(client, agent, "STATE_DELETE", {"key": "temp"})
        last = await _log(client, agent, "tool_call", {"tool": "refund"})
        res = await _state_after_everything(client, agent)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["event_count"] == 10_004
    assert body["truncated"] is False
    assert body["state"]["plan"] == "pro"
    assert "temp" not in body["state"]
    assert body["state"]["_last_event"]["event_id"] == last["event_id"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_events_at_ignores_a_delete_whose_key_is_not_a_string():
    """Regression: STATE_DELETE {"key": [...]} raised TypeError in the reduction, so
    time-travel requests for later timestamps answered 500 for the agent."""
    agent = f"time-travel-{uuid.uuid4().hex[:12]}"
    async with _live_client() as client:
        await _log(client, agent, "STATE_SET", {"plan": "pro"})
        await _log(client, agent, "STATE_DELETE", {"key": ["plan"]})
        await _log(client, agent, "STATE_DELETE", {"key": {"name": "plan"}})
        res = await _state_after_everything(client, agent)

    assert res.status_code == 200, res.text
    assert res.json()["state"] == {"plan": "pro"}
