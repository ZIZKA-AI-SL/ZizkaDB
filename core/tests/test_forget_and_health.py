"""Regression tests: forget deletes vectors before rows, never logs the value,
and /health/deep returns 503 when a required subsystem is down."""

import logging
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.memory import ForgetRequest, forget

SCOPED_TENANT = {"tenant_id": "t-1", "key_id": "k-1", "agent_id": "mine"}
SECRET_VALUE = "alice@example.com"


def _setup_forget(monkeypatch, qdrant_side_effect, embeddings_on=True):
    pool = AsyncMock()
    pool.fetch.return_value = [{"event_id": uuid.uuid4()}, {"event_id": uuid.uuid4()}]
    qdrant = AsyncMock()
    qdrant.delete.side_effect = qdrant_side_effect
    monkeypatch.setattr("api.memory.get_pool", lambda: pool)
    monkeypatch.setattr("api.memory.get_qdrant", lambda: qdrant)
    monkeypatch.setattr("api.memory.embeddings_enabled", lambda: embeddings_on)
    monkeypatch.setattr("api.memory.asyncio.sleep", AsyncMock())
    return pool, qdrant


def _body():
    return ForgetRequest(filter_key="email", filter_value=SECRET_VALUE)


# ── forget ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_forget_qdrant_down_returns_503_and_keeps_rows(monkeypatch):
    pool, qdrant = _setup_forget(monkeypatch, RuntimeError("qdrant down"))
    with pytest.raises(HTTPException) as exc:
        await forget(_body(), tenant=dict(SCOPED_TENANT))
    assert exc.value.status_code == 503
    assert qdrant.delete.await_count == 3
    pool.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_forget_retries_then_succeeds(monkeypatch):
    pool, qdrant = _setup_forget(monkeypatch, [RuntimeError("blip"), None])
    out = await forget(_body(), tenant=dict(SCOPED_TENANT))
    assert qdrant.delete.await_count == 2
    pool.execute.assert_awaited_once()
    assert out["vector_cleanup"] is True
    assert out["deleted_events"] == 2


@pytest.mark.asyncio
async def test_forget_vectors_deleted_before_rows(monkeypatch):
    order = []
    pool, qdrant = _setup_forget(monkeypatch, None)
    qdrant.delete.side_effect = lambda **kw: order.append("qdrant")
    pool.execute.side_effect = lambda *a: order.append("postgres")
    await forget(_body(), tenant=dict(SCOPED_TENANT))
    assert order == ["qdrant", "postgres"]


@pytest.mark.asyncio
async def test_forget_embeddings_off_still_deletes_rows(monkeypatch):
    pool, _ = _setup_forget(monkeypatch, RuntimeError("no qdrant"), embeddings_on=False)
    out = await forget(_body(), tenant=dict(SCOPED_TENANT))
    pool.execute.assert_awaited_once()
    assert out["vector_cleanup"] is False


@pytest.mark.asyncio
async def test_forget_never_logs_raw_value(monkeypatch, caplog):
    _setup_forget(monkeypatch, [RuntimeError("blip"), None])
    with caplog.at_level(logging.DEBUG):
        await forget(_body(), tenant=dict(SCOPED_TENANT))
    assert "GDPR forget" in caplog.text
    assert SECRET_VALUE not in caplog.text


# ── /health/deep ──────────────────────────────────────────────────────────────

def _health(monkeypatch, pg=True, redis=True, qdrant=True, embeddings_on=True):
    import main

    monkeypatch.setattr(main, "check_postgres", AsyncMock(return_value={"ok": pg}))
    monkeypatch.setattr(main, "check_redis", AsyncMock(return_value={"ok": redis}))
    monkeypatch.setattr(main, "check_qdrant", AsyncMock(return_value={"ok": qdrant}))
    monkeypatch.setattr("services.entitlements.embeddings_enabled", lambda: embeddings_on)
    return TestClient(main.app).get("/health/deep")


def test_health_deep_all_ok(monkeypatch):
    r = _health(monkeypatch)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_deep_redis_down_is_503(monkeypatch):
    r = _health(monkeypatch, redis=False)
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"]["ok"] is False


def test_health_deep_qdrant_optional_when_embeddings_off(monkeypatch):
    r = _health(monkeypatch, qdrant=False, embeddings_on=False)
    assert r.status_code == 200
    assert r.json()["checks"]["qdrant"]["required"] is False


def test_health_deep_qdrant_required_when_embeddings_on(monkeypatch):
    r = _health(monkeypatch, qdrant=False, embeddings_on=True)
    assert r.status_code == 503


def test_health_shallow_stays_200(monkeypatch):
    import main

    assert TestClient(main.app).get("/health").status_code == 200
