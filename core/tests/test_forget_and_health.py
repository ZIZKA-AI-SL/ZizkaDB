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


class _FakeTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    """Stands in for the asyncpg connection forget() acquires: serves the
    id SELECT and records every execute (advisory locks, DELETE) in order."""

    def __init__(self, rows, on_execute=None, deleted=None):
        self.rows = rows
        self.executed = []
        self._on_execute = on_execute
        # Rows the DELETE actually removes (fewer when another erasure got there first).
        self.deleted = len(rows) if deleted is None else deleted

    def transaction(self):
        return _FakeTxn()

    async def execute(self, query, *args):
        self.executed.append((query, args))
        if self._on_execute is not None:
            self._on_execute(query, args)
        return "OK"

    async def fetch(self, query, *args):
        if "DELETE FROM events" not in query:
            return self.rows
        # The delete-and-tombstone statement returns how many rows it deleted,
        # plus the tombstones to seal with a receipt (none here: the
        # real-Postgres tests cover receipts).
        await self.execute(query, *args)
        return [{"deleted": self.deleted}]

    def delete_count(self):
        return sum(1 for q, _ in self.executed if "DELETE FROM events" in q)

    def lock_args(self):
        return [a[0] for q, a in self.executed if "pg_advisory_xact_lock" in q]

    def lock_queries(self):
        return [q for q, _ in self.executed if "pg_advisory_xact_lock" in q]


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquire(self._conn)


def _setup_forget(monkeypatch, qdrant_side_effect, embeddings_on=True):
    rows = [{"event_id": uuid.uuid4()}, {"event_id": uuid.uuid4()}]
    conn = _FakeConn(rows)
    pool = _FakePool(conn)
    qdrant = AsyncMock()
    qdrant.delete.side_effect = qdrant_side_effect
    monkeypatch.setattr("api.memory.get_pool", lambda: pool)
    monkeypatch.setattr("api.memory.get_qdrant", lambda: qdrant)
    monkeypatch.setattr("api.memory.embeddings_enabled", lambda: embeddings_on)
    monkeypatch.setattr("api.memory.asyncio.sleep", AsyncMock())
    return pool, conn, qdrant


def _body():
    return ForgetRequest(filter_key="email", filter_value=SECRET_VALUE)


# ── forget ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_forget_qdrant_down_returns_503_and_keeps_rows(monkeypatch):
    pool, conn, qdrant = _setup_forget(monkeypatch, RuntimeError("qdrant down"))
    with pytest.raises(HTTPException) as exc:
        await forget(_body(), tenant=dict(SCOPED_TENANT))
    assert exc.value.status_code == 503
    assert qdrant.delete.await_count == 3
    assert conn.delete_count() == 0


@pytest.mark.asyncio
async def test_forget_retries_then_succeeds(monkeypatch):
    pool, conn, qdrant = _setup_forget(monkeypatch, [RuntimeError("blip"), None])
    out = await forget(_body(), tenant=dict(SCOPED_TENANT))
    assert qdrant.delete.await_count == 2
    assert conn.delete_count() == 1
    assert out["vector_cleanup"] is True
    assert out["deleted_events"] == 2


@pytest.mark.asyncio
async def test_forget_vectors_deleted_before_rows(monkeypatch):
    order = []
    rows = [{"event_id": uuid.uuid4()}, {"event_id": uuid.uuid4()}]

    def on_execute(query, args):
        if "DELETE FROM events" in query:
            order.append("postgres")

    conn = _FakeConn(rows, on_execute=on_execute)
    qdrant = AsyncMock()
    qdrant.delete.side_effect = lambda **kw: order.append("qdrant")
    monkeypatch.setattr("api.memory.get_pool", lambda: _FakePool(conn))
    monkeypatch.setattr("api.memory.get_qdrant", lambda: qdrant)
    monkeypatch.setattr("api.memory.embeddings_enabled", lambda: True)
    monkeypatch.setattr("api.memory.asyncio.sleep", AsyncMock())
    await forget(_body(), tenant=dict(SCOPED_TENANT))
    assert order == ["qdrant", "postgres"]


@pytest.mark.asyncio
async def test_forget_embeddings_off_still_deletes_rows(monkeypatch):
    pool, conn, _ = _setup_forget(monkeypatch, RuntimeError("no qdrant"), embeddings_on=False)
    out = await forget(_body(), tenant=dict(SCOPED_TENANT))
    assert conn.delete_count() == 1
    assert out["vector_cleanup"] is False


@pytest.mark.asyncio
async def test_forget_never_logs_raw_value(monkeypatch, caplog):
    _setup_forget(monkeypatch, [RuntimeError("blip"), None])
    with caplog.at_level(logging.DEBUG):
        await forget(_body(), tenant=dict(SCOPED_TENANT))
    assert "GDPR forget" in caplog.text
    assert SECRET_VALUE not in caplog.text


@pytest.mark.asyncio
async def test_forget_locks_each_event_before_deleting(monkeypatch):
    # #234: forget must hold the per-event advisory lock while it deletes
    # vectors and rows, so the embedding worker cannot recreate a vector
    # for an event being forgotten. All locks are taken in one statement, in
    # byte-wise id order (the order 0.5.0 used, so mixed images can't
    # deadlock during a rolling deploy), and always precede the row DELETE.
    pool, conn, qdrant = _setup_forget(monkeypatch, None)
    await forget(_body(), tenant=dict(SCOPED_TENANT))
    assert conn.lock_args() == [[r["event_id"] for r in conn.rows]]
    assert "hashtext(id)" in conn.lock_queries()[0]
    assert 'COLLATE "C" AS id' in conn.lock_queries()[0]
    assert "ORDER BY id" in conn.lock_queries()[0]
    queries = [q for q, _ in conn.executed]
    delete_idx = next(i for i, q in enumerate(queries) if "DELETE FROM events" in q)
    lock_idxs = [i for i, q in enumerate(queries) if "pg_advisory_xact_lock" in q]
    assert lock_idxs and max(lock_idxs) < delete_idx


@pytest.mark.asyncio
async def test_forget_reports_the_rows_its_delete_removed(monkeypatch):
    # An overlapping forget already removed one of the two selected rows: the
    # response counts what this call's DELETE removed, not what it selected.
    rows = [{"event_id": uuid.uuid4()}, {"event_id": uuid.uuid4()}]
    conn = _FakeConn(rows, deleted=1)
    monkeypatch.setattr("api.memory.get_pool", lambda: _FakePool(conn))
    monkeypatch.setattr("api.memory.get_qdrant", AsyncMock)
    monkeypatch.setattr("api.memory.embeddings_enabled", lambda: True)
    out = await forget(_body(), tenant=dict(SCOPED_TENANT))
    assert out["deleted_events"] == 1
    assert out["message"].startswith("Deleted 1 events.")


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
