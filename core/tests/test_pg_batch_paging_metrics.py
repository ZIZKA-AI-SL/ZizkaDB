"""Batch writes, cursor paging and /metrics against a real Postgres."""

import pytest
from fastapi import Response
from fastapi.testclient import TestClient

from api.events import LogBatchRequest, log_events_batch, query_events

pytestmark = pytest.mark.postgres

AGENT = "bot"


async def _tenant(pool) -> dict:
    tenant_id = str(await pool.fetchval(
        "INSERT INTO tenants (name) VALUES ('acme') RETURNING tenant_id"
    ))
    return {"tenant_id": tenant_id, "agent_id": AGENT}


async def test_batch_writes_in_order_with_parent_index_and_partial_failure(pg_db):
    tenant = await _tenant(pg_db)
    body = LogBatchRequest(events=[
        {"agent": AGENT, "event": "user_message", "data": {"text": "hi"}},
        {"agent": AGENT, "event": "tool_call", "data": {"tool": "x"}, "parent_index": 0},
        {"agent": AGENT, "event": "bad", "data": {"s": "nul\x00"}},
        {"agent": AGENT, "event": "orphan", "data": {}, "parent_index": 2},
        {"agent": AGENT, "event": "retry", "data": {}, "idempotency_key": "k"},
        {"agent": AGENT, "event": "retry", "data": {}, "idempotency_key": "k"},
    ])
    result = await log_events_batch(body, tenant=tenant)

    assert result["written"] == 4 and result["failed"] == 2
    ok = [r for r in result["results"] if r["ok"]]
    assert [r["index"] for r in ok] == [0, 1, 4, 5]
    assert result["results"][2]["status"] == 400
    assert "parent_index" in result["results"][3]["error"]
    assert result["results"][5]["duplicate"] is True
    parent = await pg_db.fetchval(
        "SELECT parent_event_id::text FROM events WHERE event_type = 'tool_call'"
    )
    assert parent == result["results"][0]["event_id"]
    assert await pg_db.fetchval("SELECT COUNT(*) FROM events") == 3


async def test_cursor_pages_cover_everything_once(pg_db):
    tenant = await _tenant(pg_db)
    await log_events_batch(LogBatchRequest(events=[
        {"agent": AGENT, "event": "e", "data": {"n": n}} for n in range(25)
    ]), tenant=tenant)

    seen, cursor = [], None
    while True:
        response = Response()
        page = await query_events(agent=AGENT, limit=10, offset=0, before=None, after=None,
                                  event_type=None, session_id=None, cursor=cursor,
                                  response=response, tenant=tenant)
        seen.extend(e["data"]["n"] for e in page)
        cursor = response.headers.get("x-next-cursor")
        if not cursor:
            break
    assert sorted(seen) == list(range(25)) and len(seen) == 25
    assert seen == sorted(seen, reverse=True)


async def test_metrics_needs_a_token_and_reports_gauges(pg_db, monkeypatch):
    from main import app

    client = TestClient(app)
    monkeypatch.delenv("METRICS_TOKEN", raising=False)
    assert client.get("/metrics").status_code == 404

    monkeypatch.setenv("METRICS_TOKEN", "s3cret")
    assert client.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 401

    tenant = await _tenant(pg_db)
    await log_events_batch(LogBatchRequest(events=[{"agent": AGENT, "event": "e", "data": {}}]), tenant=tenant)
    from api.metrics import collect

    text = await collect("test")
    assert 'zizkadb_build_info{version="test"} 1' in text
    assert "zizkadb_postgres_up 1" in text
    assert "zizkadb_agents 1" in text
    assert 'zizkadb_embedding_queue{status="pending"} 0' in text
