"""Embed worker retry policy against a real Postgres: backoff, the 'dead' cap,
and failing events no longer starving newer ones (#275)."""

from datetime import timedelta

import pytest

import db.connection as connection
import services.embeddings as embeddings
from workers import embed_worker

pytestmark = pytest.mark.postgres


class _Qdrant:
    def __init__(self):
        self.upserted = []

    async def upsert(self, collection_name, points):
        self.upserted.extend(p.id for p in points)


@pytest.fixture
async def worker_env(pg_db, monkeypatch):
    """A managed tenant (events start 'pending') and a stub vector store."""
    monkeypatch.setenv("DEPLOYMENT_MODE", "managed")
    monkeypatch.delenv("EMBED_SYNC", raising=False)
    qdrant = _Qdrant()
    monkeypatch.setattr(connection, "get_qdrant", lambda: qdrant)
    tenant = str(await pg_db.fetchval("INSERT INTO tenants (name) VALUES ('t') RETURNING tenant_id"))
    return tenant, qdrant


def _embedding(monkeypatch, *, fail: bool):
    async def fake(text, tenant_id):
        if fail:
            raise RuntimeError("provider down")
        return [0.0] * 1536

    monkeypatch.setattr(embeddings, "generate_embedding", fake)


async def _log(tenant, n=1):
    from services.event_write import write_event

    return [
        (await write_event(tenant_id=tenant, agent="bot", event="e", data={"i": i}))["event_id"]
        for i in range(n)
    ]


async def _state(pool, event_id):
    return await pool.fetchrow(
        """SELECT index_status, index_attempts,
                  EXTRACT(EPOCH FROM (index_retry_at - NOW())) AS retry_in
           FROM events WHERE event_id = $1::uuid""",
        event_id,
    )


async def test_failure_backs_off_and_is_not_retried_early(pg_db, worker_env, monkeypatch):
    tenant, _ = worker_env
    (event_id,) = await _log(tenant)
    _embedding(monkeypatch, fail=True)

    await embed_worker.process_pending_batch()
    first = await _state(pg_db, event_id)
    assert (first["index_status"], first["index_attempts"]) == ("failed", 1)
    assert 25 < first["retry_in"] <= 30  # RETRY_BASE_SEC

    await embed_worker.process_pending_batch()  # not due yet: untouched
    assert (await _state(pg_db, event_id))["index_attempts"] == 1


async def test_backoff_doubles_and_is_capped(pg_db, worker_env, monkeypatch):
    tenant, _ = worker_env
    (event_id,) = await _log(tenant)
    _embedding(monkeypatch, fail=True)

    for attempts_before, expected in ((1, 60), (2, 120), (7, 3600)):  # 30 * 2**7 = 3840 -> cap
        await pg_db.execute(
            "UPDATE events SET index_status = 'failed', index_attempts = $2, index_retry_at = NOW() - interval '1 second' WHERE event_id = $1::uuid",
            event_id,
            attempts_before,
        )
        await embed_worker.process_pending_batch()
        state = await _state(pg_db, event_id)
        assert state["index_attempts"] == attempts_before + 1
        assert expected - 5 < state["retry_in"] <= expected


async def test_event_is_dead_after_max_attempts_and_never_selected_again(pg_db, worker_env, monkeypatch):
    tenant, _ = worker_env
    (event_id,) = await _log(tenant)
    _embedding(monkeypatch, fail=True)
    await pg_db.execute(
        "UPDATE events SET index_status = 'failed', index_attempts = $2, index_retry_at = NOW() - interval '1 second' WHERE event_id = $1::uuid",
        event_id,
        embed_worker.MAX_ATTEMPTS - 1,
    )

    await embed_worker.process_pending_batch()
    dead = await _state(pg_db, event_id)
    assert dead["index_status"] == "dead"
    assert dead["retry_in"] is None  # nothing scheduled for a dead event

    await pg_db.execute("UPDATE events SET index_retry_at = NOW() - interval '1 day' WHERE event_id = $1::uuid", event_id)
    await embed_worker.process_pending_batch()
    state = await _state(pg_db, event_id)
    assert (state["index_status"], state["index_attempts"]) == ("dead", embed_worker.MAX_ATTEMPTS)


async def test_waiting_failures_do_not_starve_newer_events(pg_db, worker_env, monkeypatch):
    tenant, qdrant = worker_env
    stuck = await _log(tenant, n=60)
    await pg_db.execute(
        """UPDATE events SET index_status = 'failed', index_attempts = 3,
                  index_retry_at = NOW() + interval '10 minutes'
           WHERE event_id = ANY($1::uuid[])""",
        stuck,
    )
    (new_event,) = await _log(tenant)
    _embedding(monkeypatch, fail=False)

    assert await embed_worker.process_pending_batch(limit=50) == 1
    assert (await _state(pg_db, new_event))["index_status"] == "indexed"
    assert qdrant.upserted == [new_event]


async def test_event_indexes_once_its_retry_is_due(pg_db, worker_env, monkeypatch):
    tenant, qdrant = worker_env
    (event_id,) = await _log(tenant)
    await pg_db.execute(
        "UPDATE events SET index_status = 'failed', index_attempts = 2, index_retry_at = NOW() - interval '1 second' WHERE event_id = $1::uuid",
        event_id,
    )
    _embedding(monkeypatch, fail=False)

    assert await embed_worker.process_pending_batch() == 1
    state = await _state(pg_db, event_id)
    assert (state["index_status"], state["index_attempts"], state["retry_in"]) == ("indexed", 0, None)


async def test_giving_up_is_logged_as_an_error(pg_db, worker_env, monkeypatch, caplog):
    tenant, _ = worker_env
    (event_id,) = await _log(tenant)
    _embedding(monkeypatch, fail=True)
    await pg_db.execute(
        "UPDATE events SET index_status = 'failed', index_attempts = $2, index_retry_at = NOW() - interval '1 second' WHERE event_id = $1::uuid",
        event_id,
        embed_worker.MAX_ATTEMPTS - 1,
    )

    with caplog.at_level("ERROR", logger="embed_worker"):
        await embed_worker.process_pending_batch()

    assert any("giving up on event" in r.getMessage() and event_id in r.getMessage() for r in caplog.records)


async def test_large_attempt_counts_do_not_overflow_the_backoff(pg_db, worker_env, monkeypatch):
    tenant, _ = worker_env
    (event_id,) = await _log(tenant)
    _embedding(monkeypatch, fail=True)
    monkeypatch.setattr(embed_worker, "MAX_ATTEMPTS", 5000)
    await pg_db.execute(
        "UPDATE events SET index_status = 'failed', index_attempts = 1500, index_retry_at = NOW() - interval '1 second' WHERE event_id = $1::uuid",
        event_id,
    )

    await embed_worker.process_pending_batch()

    state = await _state(pg_db, event_id)
    assert (state["index_status"], state["index_attempts"]) == ("failed", 1501)
    assert 3595 < state["retry_in"] <= 3600  # RETRY_MAX_SEC
