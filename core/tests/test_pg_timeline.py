"""GET /v1/agents/{id}/timeline against real Postgres: buckets, points, errors."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

import services.timeline as timeline
from api.deps import get_tenant
from main import app
from services.event_write import write_event

pytestmark = pytest.mark.postgres

AGENT = "support-bot"
T0 = datetime(2026, 11, 12, 14, 0, tzinfo=timezone.utc)


async def _new_tenant(pool, name: str) -> str:
    return str(await pool.fetchval("INSERT INTO tenants (name) VALUES ($1) RETURNING tenant_id", name))


async def _log(tenant_id, event, minutes, data=None, session="chat_1", parent=None, agent=AGENT):
    row = await write_event(
        tenant_id=tenant_id, agent=agent, event=event, data=data or {"step": event},
        session_id=session, parent_id=parent, occurred_at=T0 + timedelta(minutes=minutes),
    )
    return row["event_id"]


@pytest.fixture
async def chat(pg_db):
    """One chat that fails at a tool, one that succeeds, and another agent's event."""
    tenant_id = await _new_tenant(pg_db, "alpha")
    u = await _log(tenant_id, "user_message", 1)
    c = await _log(tenant_id, "tool_call", 2, parent=u)
    err = await _log(tenant_id, "tool_error", 3, {"error": "TimeoutError"}, parent=c)
    # An ordinary type that carries an error in its data counts as an error too.
    soft = await _log(tenant_id, "tool_result", 40, {"error": "NotFound"}, session="chat_2")
    ok = await _log(tenant_id, "assistant_response", 41, session="chat_2")
    await _log(tenant_id, "user_message", 5, agent="billing-bot")
    return {"tenant_id": tenant_id, "user": u, "error": err, "soft": soft, "ok": ok}


@pytest.fixture
async def api_as():
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    def _as(tenant_id: str, agent_id: str | None = None) -> httpx.AsyncClient:
        tenant = {"tenant_id": tenant_id, "user_id": None}
        if agent_id:
            tenant["agent_id"] = agent_id
        app.dependency_overrides[get_tenant] = lambda: tenant
        return client

    try:
        yield _as
    finally:
        app.dependency_overrides.pop(get_tenant, None)
        await client.aclose()


def _window(hours: float = 1):
    return {"from": T0.isoformat(), "to": (T0 + timedelta(hours=hours)).isoformat()}


async def test_counts_events_per_bucket_and_type(chat, api_as):
    r = await api_as(chat["tenant_id"]).get(f"/v1/agents/{AGENT}/timeline", params=_window())

    assert r.status_code == 200
    body = r.json()
    assert body["bucket_seconds"] == 60
    assert body["total"] == 5  # the other agent's event is not counted
    assert body["error_total"] == 2
    assert {t["event"]: (t["count"], t["errors"]) for t in body["types"]} == {
        "user_message": (1, 0), "tool_call": (1, 0), "tool_error": (1, 1),
        "tool_result": (1, 1), "assistant_response": (1, 0),
    }
    assert sum(b["total"] for b in body["buckets"]) == 5
    assert sum(b["errors"] for b in body["buckets"]) == 2
    first = body["buckets"][0]
    assert datetime.fromisoformat(first["start"]) == T0 + timedelta(minutes=1)
    assert first["by_type"] == {"user_message": 1}
    assert first["errors_by_type"] == {}
    # Errors per type add up to the bucket's errors, so a spike splits cleanly.
    merged: dict[str, int] = {}
    for b in body["buckets"]:
        assert sum(b["errors_by_type"].values()) == b["errors"]
        for t, n in b["errors_by_type"].items():
            merged[t] = merged.get(t, 0) + n
    assert merged == {"tool_error": 1, "tool_result": 1}


async def test_small_window_returns_every_event_without_payload(chat, api_as):
    body = (await api_as(chat["tenant_id"]).get(f"/v1/agents/{AGENT}/timeline", params=_window())).json()

    assert [e["event_id"] for e in body["events"]] == [
        chat["user"], *[e["event_id"] for e in body["events"][1:3]], chat["soft"], chat["ok"]
    ]
    error_event = next(e for e in body["events"] if e["event_id"] == chat["error"])
    assert error_event["is_error"] is True
    assert error_event["session_id"] == "chat_1"
    assert error_event["parent_id"]
    assert "data" not in error_event


async def test_errors_are_listed_individually_even_when_events_are_grouped(chat, api_as, monkeypatch):
    monkeypatch.setattr(timeline, "POINT_LIMIT", 2)

    body = (await api_as(chat["tenant_id"]).get(f"/v1/agents/{AGENT}/timeline", params=_window())).json()

    assert body["events"] is None
    assert [e["event_id"] for e in body["errors"]] == [chat["error"], chat["soft"]]
    assert body["errors_truncated"] is False


async def test_error_list_keeps_the_newest_when_capped(chat, api_as, monkeypatch):
    monkeypatch.setattr(timeline, "ERROR_LIMIT", 1)

    body = (await api_as(chat["tenant_id"]).get(f"/v1/agents/{AGENT}/timeline", params=_window())).json()

    assert [e["event_id"] for e in body["errors"]] == [chat["soft"]]
    assert body["errors_truncated"] is True


async def test_bucket_size_follows_the_window(chat, api_as):
    client = api_as(chat["tenant_id"])
    day = (await client.get(f"/v1/agents/{AGENT}/timeline", params=_window(24))).json()
    month = (await client.get(f"/v1/agents/{AGENT}/timeline", params=_window(24 * 30))).json()

    assert day["bucket_seconds"] == 900
    assert month["bucket_seconds"] == 6 * 3600
    assert day["total"] == month["total"] == 5


async def test_rejects_a_reversed_or_oversized_window(chat, api_as):
    client = api_as(chat["tenant_id"])
    reversed_ = await client.get(
        f"/v1/agents/{AGENT}/timeline", params={"from": _window()["to"], "to": _window()["from"]}
    )
    huge = await client.get(f"/v1/agents/{AGENT}/timeline", params=_window(24 * 400))

    assert reversed_.status_code == 400
    assert huge.status_code == 400


async def test_a_key_scoped_to_another_agent_cannot_read_it(chat, api_as):
    r = await api_as(chat["tenant_id"], agent_id="billing-bot").get(
        f"/v1/agents/{AGENT}/timeline", params=_window()
    )

    assert r.status_code in (403, 404)


async def test_another_tenant_sees_nothing(chat, api_as, pg_db):
    other = await _new_tenant(pg_db, "beta")
    body = (await api_as(other).get(f"/v1/agents/{AGENT}/timeline", params=_window())).json()

    assert body["total"] == 0
    assert body["events"] == []
    assert body["errors"] == []


def test_bucket_ladder_keeps_bucket_count_bounded():
    for hours in (1, 6, 24, 24 * 7, 24 * 30, 24 * 366):
        size = timeline.bucket_seconds(T0, T0 + timedelta(hours=hours))
        assert hours * 3600 / size <= timeline.MAX_BUCKETS + 1
