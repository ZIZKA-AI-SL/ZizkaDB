"""Regression tests for the embedding worker / GDPR forget race (#234)
and the poison-pill / DLQ fix.

The worker used to select pending events, generate embeddings (slow), and
then upsert to Qdrant unconditionally — so an event deleted by GDPR
forget() in the meantime got its vector recreated as an orphan. The fix
coordinates both paths on the per-event advisory lock: the worker
re-checks existence inside the lock before upserting, and forget() holds
the same locks while it deletes vectors and rows.

The DLQ tests verify that:
- Failed events get ``retry_count`` incremented and ``next_retry_at``
  set with exponential backoff.
- Events that exceed ``MAX_RETRIES`` are moved to ``dead_letter`` status
  and are never retried.
- The time-gated query hides events whose ``next_retry_at`` is in the
  future so they don't starve fresh events.

Infrastructure-free: an in-memory fake pool/conn stands in for asyncpg,
following the fake-conn convention of test_entitlements.py.
"""

import uuid
from datetime import datetime, timezone
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
        now = datetime.now(timezone.utc)
        return [
            dict(r)
            for r in self.table.values()
            if r["index_status"] in ("pending", "failed")
            and (r.get("next_retry_at") is None or r.get("next_retry_at") <= now)
        ]

    def acquire(self):
        return _FakeAcquire(self.conn)

    async def execute(self, query, *args):
        # Handle the new _mark_failed SQL patterns.
        if "dead_letter" in query:
            # Dead-letter update: args = (retry_count, last_error, event_id)
            event_id = args[-1]
            self.log.append(("mark_dead_letter", str(event_id)))
            row = self.table.get(event_id)
            if row is not None:
                row["index_status"] = "dead_letter"
                row["retry_count"] = args[0]
                row["last_error"] = args[1]
        elif "index_status  = 'failed'" in query or "index_status = 'failed'" in query:
            # Backoff update: args = (retry_count, next_retry_at, last_error, event_id)
            # OR legacy: args = (event_id,) — detect by arg count.
            if len(args) == 4:
                event_id = args[-1]
                self.log.append(("mark_failed_backoff", str(event_id)))
                row = self.table.get(event_id)
                if row is not None:
                    row["index_status"] = "failed"
                    row["retry_count"] = args[0]
                    row["next_retry_at"] = args[1]
                    row["last_error"] = args[2]
            else:
                event_id = args[-1]
                self.log.append(("mark_failed", str(event_id)))
                row = self.table.get(event_id)
                if row is not None:
                    row["index_status"] = "failed"
        elif "index_status = 'indexed'" in query:
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


def _row(index_status="pending", retry_count=0, next_retry_at=None):
    return {
        "event_id": uuid.uuid4(),
        "tenant_id": "t-1",
        "agent_id": "agent-1",
        "event_type": "note",
        "data": {"text": "hello"},
        "index_status": index_status,
        "retry_count": retry_count,
        "next_retry_at": next_retry_at,
        "last_error": None,
    }


def _setup(monkeypatch, rows, embedding_side_effect=None, embedding_result=None,
           max_retries=None):
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

    if max_retries is not None:
        monkeypatch.setattr(embed_worker, "MAX_RETRIES", max_retries)

    return pool, qdrant


# ─────────────────────────────────────────
# EXISTING TESTS (GDPR / #234 regression)
# ─────────────────────────────────────────

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


# ─────────────────────────────────────────
# NEW TESTS — Exponential Backoff & DLQ
# ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_failed_event_gets_retry_count_incremented(monkeypatch):
    """First failure: retry_count goes from 0 → 1 and next_retry_at
    is set to a future timestamp."""
    row = _row()
    pool, _ = _setup(
        monkeypatch, [row], embedding_result=RuntimeError("timeout"),
        max_retries=5,
    )
    await embed_worker.process_pending_batch()

    event = pool.table[row["event_id"]]
    assert event["index_status"] == "failed"
    assert event["retry_count"] == 1
    assert event["next_retry_at"] is not None
    assert event["next_retry_at"] > datetime.now(timezone.utc)
    assert event["last_error"] == "timeout"


@pytest.mark.asyncio
async def test_backoff_hides_event_from_next_batch(monkeypatch):
    """After a failure, the event's next_retry_at is in the future,
    so the next process_pending_batch() should NOT pick it up."""
    row = _row()
    pool, _ = _setup(
        monkeypatch, [row], embedding_result=RuntimeError("rate limit"),
        max_retries=5,
    )
    # First call → fails, sets next_retry_at in the future.
    await embed_worker.process_pending_batch()
    event = pool.table[row["event_id"]]
    assert event["index_status"] == "failed"
    assert event["next_retry_at"] > datetime.now(timezone.utc)

    # Second call → the event should be hidden (next_retry_at is future).
    processed = await embed_worker.process_pending_batch()
    assert processed == 0
    # retry_count should NOT have increased — event was skipped entirely.
    assert pool.table[row["event_id"]]["retry_count"] == 1


@pytest.mark.asyncio
async def test_event_moves_to_dead_letter_after_max_retries(monkeypatch):
    """Simulate an event that has already failed (max_retries - 1) times.
    The next failure should move it to 'dead_letter'."""
    max_r = 3
    # Pre-seed the row as if it already failed twice.
    row = _row(index_status="failed", retry_count=max_r - 1)
    # Set next_retry_at to the past so it's eligible.
    row["next_retry_at"] = datetime(2000, 1, 1, tzinfo=timezone.utc)

    pool, _ = _setup(
        monkeypatch, [row], embedding_result=RuntimeError("permanent error"),
        max_retries=max_r,
    )
    await embed_worker.process_pending_batch()

    event = pool.table[row["event_id"]]
    assert event["index_status"] == "dead_letter"
    assert event["retry_count"] == max_r
    assert event["last_error"] == "permanent error"


@pytest.mark.asyncio
async def test_dead_letter_events_are_never_retried(monkeypatch):
    """Once an event is dead_letter, the worker never picks it up."""
    row = _row(index_status="dead_letter", retry_count=5)
    pool, _ = _setup(monkeypatch, [row])

    processed = await embed_worker.process_pending_batch()
    assert processed == 0
    assert pool.table[row["event_id"]]["index_status"] == "dead_letter"


@pytest.mark.asyncio
async def test_fresh_pending_event_not_blocked_by_failed_with_backoff(monkeypatch):
    """A failed event in backoff (next_retry_at in the future) must NOT
    block a fresh pending event from being processed."""
    from datetime import timedelta

    failed_row = _row(index_status="failed", retry_count=2)
    failed_row["next_retry_at"] = datetime.now(timezone.utc) + timedelta(hours=1)

    fresh_row = _row()  # pending, next_retry_at=None

    pool, qdrant = _setup(monkeypatch, [failed_row, fresh_row])
    processed = await embed_worker.process_pending_batch()

    assert processed == 1
    assert qdrant.upserted == [str(fresh_row["event_id"])]
    # Failed event should NOT have been touched at all.
    assert pool.table[failed_row["event_id"]]["index_status"] == "failed"
    assert pool.table[failed_row["event_id"]]["retry_count"] == 2


@pytest.mark.asyncio
async def test_next_retry_at_helper_exponential(monkeypatch):
    """Verify the backoff formula produces increasing delays."""
    # Freeze BASE_BACKOFF_SEC to a known value for deterministic testing.
    monkeypatch.setattr(embed_worker, "BASE_BACKOFF_SEC", 10.0)
    now = datetime.now(timezone.utc)

    t1 = embed_worker._next_retry_at(1)
    t2 = embed_worker._next_retry_at(2)
    t3 = embed_worker._next_retry_at(3)

    # 10 * 2^1 = 20s, 10 * 2^2 = 40s, 10 * 2^3 = 80s
    assert t1 > now
    assert t2 > t1
    assert t3 > t2


@pytest.mark.asyncio
async def test_retry_then_success(monkeypatch):
    """An event that was previously failed but whose backoff has expired
    should be retried and indexed successfully."""
    row = _row(index_status="failed", retry_count=1)
    # Backoff expired — eligible for retry.
    row["next_retry_at"] = datetime(2000, 1, 1, tzinfo=timezone.utc)

    pool, qdrant = _setup(monkeypatch, [row])
    processed = await embed_worker.process_pending_batch()

    assert processed == 1
    assert pool.table[row["event_id"]]["index_status"] == "indexed"
    assert qdrant.upserted == [str(row["event_id"])]


@pytest.mark.asyncio
async def test_last_error_is_capped(monkeypatch):
    """Error messages longer than 2000 chars are truncated to avoid
    unbounded text storage."""
    row = _row()
    long_error = "x" * 5000
    pool, _ = _setup(
        monkeypatch, [row], embedding_result=RuntimeError(long_error),
        max_retries=5,
    )
    await embed_worker.process_pending_batch()

    event = pool.table[row["event_id"]]
    assert len(event["last_error"]) <= 2000
