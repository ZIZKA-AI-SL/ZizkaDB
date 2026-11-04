"""Regression tests for the embedding worker / GDPR forget race (#234).

The worker used to select pending events, generate embeddings (slow), and
then upsert to Qdrant unconditionally — so an event deleted by GDPR
forget() in the meantime got its vector recreated as an orphan. The fix
coordinates both paths on the per-event advisory lock: the worker
re-checks existence inside the lock before upserting, and forget() holds
the same locks while it deletes vectors and rows.

Infrastructure-free: an in-memory fake pool/conn stands in for asyncpg,
following the fake-conn convention of test_entitlements.py.
"""

import uuid
from unittest.mock import AsyncMock

import pytest

from workers import embed_worker


class _FakeTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    """The connection the worker acquires for its indexing critical
    section. Shares the pool's in-memory table and call log."""

    def __init__(self, pool):
        self._pool = pool

    def transaction(self):
        return _FakeTxn()

    async def execute(self, query, *args):
        pool = self._pool
        if "pg_advisory_xact_lock" in query:
            pool.log.append(("lock", args[0]))
        elif "index_status = 'indexed'" in query:
            pool.log.append(("update_indexed", str(args[-1])))
            row = pool.table.get(args[-1])
            if row is not None:
                row["index_status"] = "indexed"
        return "OK"

    async def fetchval(self, query, *args):
        if "FROM events" in query:
            return 1 if args[0] in self._pool.table else None
        return None


class _FakePool:
    def __init__(self, rows):
        self.table = {r["event_id"]: dict(r) for r in rows}
        self.log = []
        self.conn = _FakeConn(self)

    async def fetch(self, query, *args):
        return [
            dict(r)
            for r in self.table.values()
            if r["index_status"] in ("pending", "failed")
        ]

    def acquire(self):
        return _FakeAcquire(self.conn)

    async def fetchval(self, query, *args):
        if "index_attempts = index_attempts + 1" in query:
            self.log.append(("mark_failed", str(args[0])))
            row = self.table.get(args[0])
            if row is None:
                return None
            row["index_attempts"] = row.get("index_attempts", 0) + 1
            row["index_status"] = "dead" if row["index_attempts"] >= args[1] else "failed"
            return row["index_status"]
        return None

    async def execute(self, query, *args):
        if "index_status = 'indexed'" in query:
            self.log.append(("update_indexed", str(args[-1])))
            row = self.table.get(args[-1])
            if row is not None:
                row["index_status"] = "indexed"
        return "OK"


class _FakeQdrant:
    def __init__(self, pool):
        self._pool = pool
        self.upserted = []

    async def upsert(self, collection_name, points):
        for point in points:
            self.upserted.append(point.id)
            self._pool.log.append(("upsert", point.id))


def _row(index_status="pending"):
    return {
        "event_id": uuid.uuid4(),
        "tenant_id": "t-1",
        "agent_id": "agent-1",
        "event_type": "note",
        "data": {"text": "hello"},
        "index_status": index_status,
    }


def _setup(monkeypatch, rows, embedding_side_effect=None, embedding_result=None):
    pool = _FakePool(rows)
    qdrant = _FakeQdrant(pool)
    # The worker imports get_pool/get_qdrant/init_db/close_db from
    # db.connection at call time; another test module replaces that
    # module in sys.modules with a minimal stub, so patch the names on
    # the exact module object the from-import will resolve.
    import importlib

    db_connection = importlib.import_module("db.connection")
    monkeypatch.setattr(db_connection, "get_pool", lambda: pool, raising=False)
    monkeypatch.setattr(db_connection, "get_qdrant", lambda: qdrant, raising=False)
    monkeypatch.setattr(db_connection, "init_db", AsyncMock(), raising=False)
    monkeypatch.setattr(db_connection, "close_db", AsyncMock(), raising=False)

    async def fake_generate_embedding(text, tenant_id):
        if embedding_side_effect is not None:
            embedding_side_effect(pool)
        if isinstance(embedding_result, Exception):
            raise embedding_result
        return [0.1, 0.2, 0.3] if embedding_result is None else embedding_result

    monkeypatch.setattr(
        "services.embeddings.generate_embedding", fake_generate_embedding
    )
    return pool, qdrant


@pytest.mark.asyncio
async def test_worker_does_not_recreate_vector_for_forgotten_event(monkeypatch):
    """The #234 race: the worker selects a pending event, forget()
    deletes it while the embedding is being generated, and the worker
    must not upsert the vector afterwards."""
    row = _row()
    event_id = row["event_id"]

    def forget_commits(pool):
        pool.table.pop(event_id, None)

    pool, qdrant = _setup(
        monkeypatch, [row], embedding_side_effect=forget_commits
    )
    processed = await embed_worker.process_pending_batch()
    assert processed == 0
    assert qdrant.upserted == []


@pytest.mark.asyncio
async def test_worker_indexes_pending_event_under_lock(monkeypatch):
    row = _row()
    pool, qdrant = _setup(monkeypatch, [row])
    processed = await embed_worker.process_pending_batch()
    assert processed == 1
    assert qdrant.upserted == [str(row["event_id"])]
    assert pool.table[row["event_id"]]["index_status"] == "indexed"
    kinds = [kind for kind, _ in pool.log]
    assert "lock" in kinds
    assert kinds.index("lock") < kinds.index("upsert")


@pytest.mark.asyncio
async def test_worker_marks_failed_when_embedding_fails(monkeypatch):
    row = _row()
    pool, qdrant = _setup(
        monkeypatch, [row], embedding_result=RuntimeError("provider down")
    )
    processed = await embed_worker.process_pending_batch()
    assert processed == 0
    assert qdrant.upserted == []
    assert pool.table[row["event_id"]]["index_status"] == "failed"



async def test_worker_loop_survives_a_failing_batch(monkeypatch):
    """A batch that raises (e.g. Postgres briefly down) must not end the worker."""
    import asyncio
    import importlib

    calls = {"n": 0}

    async def flaky_batch():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("postgres unreachable")
        if calls["n"] == 3:
            raise asyncio.CancelledError  # stop the loop after it recovered
        return 0

    db_connection = importlib.import_module("db.connection")
    monkeypatch.setattr(db_connection, "init_db", AsyncMock(), raising=False)
    monkeypatch.setattr(db_connection, "close_db", AsyncMock(), raising=False)
    monkeypatch.setattr(embed_worker, "process_pending_batch", flaky_batch)
    monkeypatch.setattr(embed_worker, "POLL_INTERVAL", 0.0)

    with pytest.raises(asyncio.CancelledError):
        await embed_worker.main()

    assert calls["n"] == 3  # kept polling after the failure


async def test_batch_query_skips_events_not_yet_due(monkeypatch):
    """The unit suite has no real Postgres: at least pin the retry-time filter."""
    seen = {}
    row = _row()
    pool, _ = _setup(monkeypatch, [row], embedding_result=RuntimeError("provider down"))
    original_fetch = pool.fetch

    async def fetch(query, *args):
        seen["query"] = query
        return await original_fetch(query, *args)

    pool.fetch = fetch
    await embed_worker.process_pending_batch()

    assert "index_retry_at IS NULL OR index_retry_at <= NOW()" in seen["query"]
