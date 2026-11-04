"""Embed worker retry policy against a real Postgres: backoff, the 'dead' cap,
and failing events no longer starving newer ones (#275)."""

import pytest

import db.connection as connection
import services.embeddings as embeddings
from services import error_text
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
    # Pin the retry policy so the timings below do not depend on the
    # environment the suite runs in.
    monkeypatch.setattr(embed_worker, "MAX_ATTEMPTS", 10)
    monkeypatch.setattr(embed_worker, "RETRY_BASE_SEC", 30.0)
    monkeypatch.setattr(embed_worker, "RETRY_MAX_SEC", 3600.0)
    monkeypatch.setenv("DEPLOYMENT_MODE", "managed")
    monkeypatch.delenv("EMBED_SYNC", raising=False)
    qdrant = _Qdrant()
    monkeypatch.setattr(connection, "get_qdrant", lambda: qdrant)
    tenant = str(await pg_db.fetchval("INSERT INTO tenants (name) VALUES ('t') RETURNING tenant_id"))
    return tenant, qdrant


def _embedding(monkeypatch, *, fail: bool):
    async def fake(text, tenant_id, **kwargs):
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


async def test_reindex_script_requeues_failed_and_dead_events_with_a_fresh_budget(pg_db, worker_env):
    import importlib.util
    from pathlib import Path

    tenant, _ = worker_env
    failed, dead, skipped = await _log(tenant, n=3)
    await pg_db.execute(
        "UPDATE events SET index_status = 'failed', index_attempts = 9, index_retry_at = NOW() + interval '1 hour' WHERE event_id = $1::uuid",
        failed,
    )
    await pg_db.execute(
        "UPDATE events SET index_status = 'dead', index_attempts = 10, index_retry_at = NULL WHERE event_id = $1::uuid",
        dead,
    )
    await pg_db.execute("UPDATE events SET index_status = 'skipped' WHERE event_id = $1::uuid", skipped)

    script = Path(__file__).resolve().parents[2] / "scripts" / "reindex-events.py"
    spec = importlib.util.spec_from_file_location("reindex_events", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.REQUEUE_SQL is embed_worker.REQUEUE_SQL  # one definition
    await pg_db.execute(embed_worker.REQUEUE_SQL)

    for event_id in (failed, dead):
        state = await _state(pg_db, event_id)
        assert (state["index_status"], state["index_attempts"], state["retry_in"]) == ("pending", 0, None)
    assert (await _state(pg_db, skipped))["index_status"] == "skipped"


# ── index_last_error: why the latest attempt failed ────────────────────────


async def _last_error(pool, event_id):
    return await pool.fetchval(
        "SELECT index_last_error FROM events WHERE event_id = $1::uuid", event_id
    )


async def test_failure_keeps_the_reason_until_the_event_is_indexed(pg_db, worker_env, monkeypatch):
    tenant, _ = worker_env
    (event_id,) = await _log(tenant)
    _embedding(monkeypatch, fail=True)

    await embed_worker.process_pending_batch()
    assert await _last_error(pg_db, event_id) == "RuntimeError: provider down"

    await pg_db.execute(embed_worker.REQUEUE_SQL)  # a re-queue keeps it
    assert await _last_error(pg_db, event_id) == "RuntimeError: provider down"

    _embedding(monkeypatch, fail=False)
    assert await embed_worker.process_pending_batch() == 1
    assert (await _state(pg_db, event_id))["index_status"] == "indexed"
    assert await _last_error(pg_db, event_id) is None


async def test_an_unsafe_failure_message_is_still_recorded_safely(pg_db, worker_env, monkeypatch):
    """A NUL byte (which Postgres TEXT refuses), a token and a 5,000-character message:
    the failure is still counted, and the stored reason carries none of them."""
    tenant, _ = worker_env
    (event_id,) = await _log(tenant)

    async def fake(text, tenant_id, **kwargs):
        raise RuntimeError("bad\x00input Authorization: Bearer s3cr3t-token-value " + "word " * 1000)

    monkeypatch.setattr(embeddings, "generate_embedding", fake)
    await embed_worker.process_pending_batch()

    state = await _state(pg_db, event_id)
    assert (state["index_status"], state["index_attempts"]) == ("failed", 1)
    reason = await _last_error(pg_db, event_id)
    assert reason.startswith("RuntimeError: bad input Authorization: [redacted] word word")
    assert "s3cr3t" not in reason and "\x00" not in reason
    assert len(reason) == error_text.MAX_CHARS


async def test_an_upgrade_adds_the_column(pg_db):
    await pg_db.execute("ALTER TABLE events DROP COLUMN index_last_error")
    await connection._run_migrations()
    assert await pg_db.fetchval(
        """SELECT data_type FROM information_schema.columns
           WHERE table_name = 'events' AND column_name = 'index_last_error'"""
    ) == "text"


async def test_a_fresh_install_from_schema_sql_has_the_column():
    """schema.sql alone (Docker's first boot) must define it: the pg_db fixture
    always runs init_db() as well, which would hide a column missing there."""
    import os
    import uuid
    from pathlib import Path
    from urllib.parse import urlsplit, urlunsplit

    import asyncpg

    admin_dsn = os.environ["ZIZKADB_TEST_DATABASE_URL"]
    database = f"zizkadb_test_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
        conn = await asyncpg.connect(urlunsplit(urlsplit(admin_dsn)._replace(path=f"/{database}")))
        try:
            await conn.execute((Path(__file__).resolve().parents[1] / "db" / "schema.sql").read_text())
            column = await conn.fetchrow(
                """SELECT data_type, is_nullable, column_default FROM information_schema.columns
                   WHERE table_name = 'events' AND column_name = 'index_last_error'"""
            )
        finally:
            await conn.close()
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        await admin.close()
    assert column is not None
    assert (column["data_type"], column["is_nullable"], column["column_default"]) == ("text", "YES", None)


async def test_a_provider_error_reaches_the_stored_reason(pg_db, worker_env, monkeypatch):
    """Through the real embedding service: it would return None for a 401 and
    log the error, so the worker asks it to raise instead."""
    import httpx

    from services.embedding_config import TenantEmbeddingConfig

    tenant, _ = worker_env
    (event_id,) = await _log(tenant)

    async def config(tenant_id):
        return TenantEmbeddingConfig(
            tenant_id=tenant_id,
            provider="openai",
            model="text-embedding-3-small",
            use_platform_key=True,
            api_key="sk-test-0123456789abcdef",
        )

    async def openai_401(text, cfg):
        request = httpx.Request("POST", "https://api.openai.com/v1/embeddings")
        httpx.Response(401, request=request).raise_for_status()

    class _NoCache:
        async def get(self, key):
            return None

    monkeypatch.setattr(embeddings, "get_tenant_embedding_config", config)
    monkeypatch.setattr(embeddings, "_openai_embedding", openai_401)
    monkeypatch.setattr(embeddings, "get_redis", lambda: _NoCache())  # never a real Redis

    await embed_worker.process_pending_batch()

    reason = await _last_error(pg_db, event_id)
    assert reason.startswith(
        "EmbeddingUnavailable: HTTPStatusError: Client error '401 Unauthorized' "
        "for url 'https://api.openai.com/v1/embeddings'"
    )
    assert "sk-test" not in reason
