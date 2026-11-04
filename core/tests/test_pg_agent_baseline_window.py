"""GET /v1/agents/{id}/baseline?window=… against a real Postgres (#279).

The window split compares each session's MAX(timestamp), which asyncpg returns
timezone-aware, with a cutoff. A naive cutoff made every window request fail."""

from datetime import timedelta

import httpx
import pytest

from api.deps import get_tenant
from main import app
from services.event_write import write_event

pytestmark = pytest.mark.postgres

AGENT = "bot"


async def _session(pool, tenant, session_id, age):
    """Two events in one session, both moved `age` into the past."""
    ids = []
    for event in ("user_message", "tool_call"):
        e = await write_event(tenant_id=tenant, agent=AGENT, event=event, data={"s": session_id}, session_id=session_id)
        ids.append(e["event_id"])
    await pool.execute("UPDATE events SET timestamp = NOW() - $2::interval WHERE event_id = ANY($1::uuid[])", ids, age)


@pytest.fixture
async def history(pg_db):
    tenant = str(await pg_db.fetchval("INSERT INTO tenants (name) VALUES ('t') RETURNING tenant_id"))
    for i in range(3):
        await _session(pg_db, tenant, f"old-{i}", timedelta(days=10))
    for i in range(2):
        await _session(pg_db, tenant, f"new-{i}", timedelta(hours=1))
    return tenant


async def _baseline(tenant, **params):
    app.dependency_overrides[get_tenant] = lambda: {"tenant_id": tenant, "user_id": None}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            return await client.get(f"/v1/agents/{AGENT}/baseline", params=params)
    finally:
        app.dependency_overrides.pop(get_tenant, None)


@pytest.mark.parametrize("window", ["24h", "7d"])
async def test_window_splits_sessions_at_the_cutoff(history, window):
    response = await _baseline(history, window=window)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["window"] == window
    assert body["recent"]["sessions"] == 2   # the two from an hour ago
    assert body["baseline"]["sessions"] == 3  # the three from ten days ago


async def test_window_wider_than_history_is_warming_up(history):
    response = await _baseline(history, window="30d")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "warming_up"  # nothing older than 30 days
    assert body["window"] == "30d"
    assert "ended earlier" in body["message"]  # not the session-count advice


async def test_idle_window_is_not_reported_as_drift(pg_db):
    """No sessions inside the window: say so instead of comparing an empty set."""
    tenant = str(await pg_db.fetchval("INSERT INTO tenants (name) VALUES ('t') RETURNING tenant_id"))
    for i in range(3):
        await _session(pg_db, tenant, f"old-{i}", timedelta(days=3))

    response = await _baseline(tenant, window="24h")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "no_current_data"  # same status as /behavior-change
    assert body["window"] == "24h"
    assert body["sessions"] == 3
    assert "drift" not in body


async def test_count_based_split_still_works(history):
    response = await _baseline(history, recent_window=5)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "warming_up"  # 5 sessions, needs at least 6


async def test_count_based_split_reports_drift(pg_db):
    """The default split (no window) also goes through the new query."""
    tenant = str(await pg_db.fetchval("INSERT INTO tenants (name) VALUES ('t') RETURNING tenant_id"))
    for i in range(6):
        await _session(pg_db, tenant, f"s-{i}", timedelta(hours=6 - i))

    response = await _baseline(tenant, recent_window=5)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["window"] is None
    assert (body["recent"]["sessions"], body["baseline"]["sessions"]) == (5, 1)
