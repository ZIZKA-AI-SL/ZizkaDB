"""Superseded facts, importance/recency ranking and duplicates in context_for/search."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from api.memory import ContextRequest, get_context
from api.search import SearchRequest, semantic_search
from services.event_write import write_event
from services.memory_rank import superseded_ids, weighted_score

pytestmark = pytest.mark.postgres

AGENT = "bot"


async def _tenant(pool) -> dict:
    tenant_id = str(await pool.fetchval("INSERT INTO tenants (name) VALUES ('acme') RETURNING tenant_id"))
    return {"tenant_id": tenant_id, "agent_id": AGENT}


async def _log(tenant, **kw):
    return await write_event(tenant_id=tenant["tenant_id"], agent=AGENT,
                             event=kw.pop("event", "fact"), data=kw.pop("data", {}), **kw)


async def test_explicit_supersedes_hides_the_old_fact(pg_db, monkeypatch):
    monkeypatch.setattr("api.search.embeddings_enabled", lambda: False)
    tenant = await _tenant(pg_db)
    old = await _log(tenant, data={"city": "user lives in Madrid"})
    new = await _log(tenant, data={"city": "user lives in Berlin"}, supersedes=old["event_id"])

    found = await semantic_search(SearchRequest(query="user lives"), tenant=tenant)
    assert [r["event_id"] for r in found["results"]] == [new["event_id"]]

    with_old = await semantic_search(SearchRequest(query="user lives", include_superseded=True), tenant=tenant)
    assert len(with_old["results"]) == 2

    context = await get_context(ContextRequest(agent=AGENT, task="where does the user live"), tenant=tenant)
    assert "Berlin" in context["context"] and "Madrid" not in context["context"]
    assert await pg_db.fetchval("SELECT COUNT(*) FROM events") == 2  # nothing deleted


async def test_supersedes_must_name_an_event_of_the_same_agent(pg_db):
    tenant = await _tenant(pg_db)
    with pytest.raises(HTTPException) as exc:
        await _log(tenant, supersedes="00000000-0000-0000-0000-000000000000")
    assert exc.value.status_code == 400


async def test_state_set_is_superseded_when_all_its_keys_change(pg_db):
    tenant = await _tenant(pg_db)
    both = await _log(tenant, event="STATE_SET", data={"plan": "free", "tz": "UTC"})
    await _log(tenant, event="STATE_SET", data={"plan": "pro"})
    partly = await superseded_ids(pg_db, tenant["tenant_id"], AGENT, [both["event_id"]])
    assert partly == set()  # tz is still current

    await _log(tenant, event="STATE_DELETE", data={"key": "tz"})
    assert await superseded_ids(pg_db, tenant["tenant_id"], AGENT, [both["event_id"]]) == {both["event_id"]}


async def test_duplicates_are_merged_in_context(pg_db):
    tenant = await _tenant(pg_db)
    for _ in range(3):
        await _log(tenant, event="note", data={"text": "same note"})
    context = await get_context(ContextRequest(agent=AGENT, task="note"), tenant=tenant)
    assert context["event_count"] == 1


async def test_importance_is_validated_and_stored_in_metadata(pg_db):
    tenant = await _tenant(pg_db)
    written = await _log(tenant, importance=0.9)
    assert await pg_db.fetchval(
        "SELECT (metadata->>'importance')::float FROM events WHERE event_id = $1", written["event_id"]
    ) == 0.9
    with pytest.raises(HTTPException):
        await _log(tenant, importance=1.5)


def test_weighted_score_prefers_important_and_recent():
    now = datetime.now(timezone.utc)
    fresh = weighted_score(1.0, now, {"importance": 0.5}, now)
    old = weighted_score(1.0, now - timedelta(days=30), {"importance": 0.5}, now)
    important = weighted_score(1.0, now, {"importance": 1.0}, now)
    assert old == pytest.approx(fresh / 2)
    assert important > fresh
    assert weighted_score(1.0, now, '{"importance": 7}', now) == fresh  # out of range → default
