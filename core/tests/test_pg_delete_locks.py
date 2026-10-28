"""Agent and account deletes racing event writes, against a real Postgres.

Each scripted test pauses a real write, erasure or delete inside its
transaction while it holds the locks that #305 and #306 were reproduced with,
runs the other side, then lets the first one finish.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
from fastapi import HTTPException

import services.event_write as event_write
from api import agents as agents_api
from api.memory import ForgetRequest, forget
import services.retention as retention
from services import account
from services.api_keys import claim_unassigned_api_key, create_api_key_record

pytestmark = pytest.mark.postgres

BLOCKED = 0.5  # long enough for a waiting statement to be stuck on a lock


@pytest.fixture(autouse=True)
def no_vectors(monkeypatch):
    async def _skip(*_args, **_kwargs):
        return None

    async def _deleted(*_args, **_kwargs):
        return True

    monkeypatch.setattr(agents_api, "_purge_agent_vectors", _skip)
    monkeypatch.setattr(account, "_purge_tenant_vectors", _skip)
    monkeypatch.setattr("api.memory._delete_vectors", _deleted)


async def _tenant(pool) -> str:
    return str(await pool.fetchval(
        "INSERT INTO tenants (name) VALUES ('acme') RETURNING tenant_id"
    ))


async def _user(pool, tenant_id: str) -> str:
    return str(await pool.fetchval(
        "INSERT INTO users (email, tenant_id) VALUES ('a@example.com', $1) RETURNING user_id",
        tenant_id,
    ))


async def _log(tenant_id: str, agent: str) -> dict:
    return await event_write.write_event(tenant_id=tenant_id, agent=agent, event="ping", data={"ok": True})


@pytest.fixture
async def pause_write(pg_db, monkeypatch):
    """``pause_write()`` holds the next write_event after it has locked its
    agents row (UPDATE or insert) and taken its sequence number.

    Teardown releases it even when a test fails, so the database can be dropped.
    """
    reached, release = asyncio.Event(), asyncio.Event()

    def arm():
        original = event_write.latest_link

        async def paused(*args, **kwargs):
            reached.set()
            await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(event_write, "latest_link", paused)
        return reached, release

    yield arm
    release.set()
    await asyncio.sleep(BLOCKED)


async def _seed_expired(pool, tenant_id: str, agent: str, n: int = 3) -> None:
    old = datetime.now(timezone.utc) - timedelta(days=30)
    for _ in range(n):
        await event_write.write_event(tenant_id=tenant_id, agent=agent, event="old", data={}, occurred_at=old)
    await pool.execute("UPDATE agents SET retention_days = 7 WHERE tenant_id = $1", tenant_id)


@pytest.fixture
async def pause_retention(pg_db, monkeypatch):
    """Hold retention right after it deleted a batch, before its counter update."""
    reached, release = asyncio.Event(), asyncio.Event()
    original = retention.delete_and_tombstone

    async def paused(*args, **kwargs):
        deleted = await original(*args, **kwargs)
        reached.set()
        await release.wait()
        return deleted

    monkeypatch.setattr(retention, "delete_and_tombstone", paused)
    yield reached, release
    release.set()
    await asyncio.sleep(BLOCKED)


async def _still_waiting(task) -> bool:
    await asyncio.sleep(BLOCKED)
    return not task.done()


async def test_agent_delete_waits_for_a_write_in_flight(pg_db, pause_write):
    tenant_id = await _tenant(pg_db)
    await _log(tenant_id, "bot")
    reached, release = pause_write()

    write = asyncio.create_task(_log(tenant_id, "bot"))
    await asyncio.wait_for(reached.wait(), 5)
    delete = asyncio.create_task(agents_api.delete_agent("bot", tenant={"tenant_id": tenant_id}))
    assert await _still_waiting(delete)

    release.set()
    written = await asyncio.wait_for(write, 5)
    result = await asyncio.wait_for(delete, 5)

    # The delete saw the write's event and removed it with the agent.
    assert not await pg_db.fetchval(
        "SELECT 1 FROM events WHERE event_id = $1::uuid", written["event_id"]
    )
    assert result["events_deleted"] == 2
    assert await pg_db.fetchval("SELECT COUNT(*) FROM agents WHERE tenant_id = $1", tenant_id) == 0
    assert await pg_db.fetchval("SELECT COUNT(*) FROM events WHERE tenant_id = $1", tenant_id) == 0


async def test_write_after_agent_delete_goes_through_the_agent_cap(pg_db):
    tenant_id = await _tenant(pg_db)
    # Two agents from before the self-hosted cap; deleting one leaves the slot full.
    for name in ("bot", "other"):
        await pg_db.execute(
            "INSERT INTO agents (agent_id, tenant_id) VALUES ($1, $2)", name, tenant_id
        )

    # Hold the agent the way delete_agent does, then start a write to it.
    async with pg_db.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        await conn.execute(
            "SELECT 1 FROM agents WHERE tenant_id = $1 AND agent_id = 'bot' FOR UPDATE", tenant_id
        )
        write = asyncio.create_task(_log(tenant_id, "bot"))
        assert await _still_waiting(write)
        await conn.execute("DELETE FROM agents WHERE tenant_id = $1 AND agent_id = 'bot'", tenant_id)
        await tx.commit()

    with pytest.raises(HTTPException) as exc:
        await asyncio.wait_for(write, 5)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "agent_limit_reached"
    assert await pg_db.fetchval(
        "SELECT array_agg(agent_id) FROM agents WHERE tenant_id = $1", tenant_id
    ) == ["other"]


async def test_write_after_agent_delete_recreates_the_agent_when_there_is_room(pg_db):
    tenant_id = await _tenant(pg_db)
    await _log(tenant_id, "bot")

    async with pg_db.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        await conn.execute(
            "SELECT 1 FROM agents WHERE tenant_id = $1 AND agent_id = 'bot' FOR UPDATE", tenant_id
        )
        write = asyncio.create_task(_log(tenant_id, "bot"))
        assert await _still_waiting(write)
        await conn.execute("DELETE FROM events WHERE tenant_id = $1", tenant_id)
        await conn.execute("DELETE FROM agents WHERE tenant_id = $1", tenant_id)
        await tx.commit()

    await asyncio.wait_for(write, 5)
    assert await pg_db.fetchval(
        "SELECT event_count FROM agents WHERE tenant_id = $1 AND agent_id = 'bot'", tenant_id
    ) == 1


async def test_account_delete_does_not_deadlock_with_a_write_in_flight(pg_db, monkeypatch, pause_write):
    monkeypatch.setenv("ENV", "production")
    tenant_id = await _tenant(pg_db)
    user_id = await _user(pg_db, tenant_id)
    await _log(tenant_id, "bot")
    reached, release = pause_write()

    write = asyncio.create_task(_log(tenant_id, "bot"))
    await asyncio.wait_for(reached.wait(), 5)
    delete = asyncio.create_task(
        account.delete_managed_account(user_id=user_id, tenant_id=tenant_id)
    )
    assert await _still_waiting(delete)

    release.set()
    await asyncio.wait_for(write, 5)
    await asyncio.wait_for(delete, 5)

    assert await pg_db.fetchval("SELECT COUNT(*) FROM tenants WHERE tenant_id = $1", tenant_id) == 0
    assert await pg_db.fetchval("SELECT COUNT(*) FROM events WHERE tenant_id = $1", tenant_id) == 0


async def test_agent_delete_racing_writes_never_fails(pg_db):
    tenant_id = await _tenant(pg_db)
    for _ in range(50):
        await _log(tenant_id, "bot")
        results = await asyncio.gather(
            agents_api.delete_agent("bot", tenant={"tenant_id": tenant_id}),
            *(_log(tenant_id, "bot") for _ in range(4)),
            return_exceptions=True,
        )
        for r in results:
            assert not isinstance(r, BaseException), repr(r)
        # The counter matches what is stored, whichever order things ran in.
        count = await pg_db.fetchval(
            "SELECT event_count FROM agents WHERE tenant_id = $1 AND agent_id = 'bot'", tenant_id
        )
        stored = await pg_db.fetchval("SELECT COUNT(*) FROM events WHERE tenant_id = $1", tenant_id)
        assert (count or 0) == stored


@pytest.mark.parametrize("mode", ["managed", "self_hosted"])
async def test_account_delete_racing_writes_never_deadlocks(pg_db, monkeypatch, mode):
    monkeypatch.setenv("ENV", "production")
    # Managed (where account delete runs) does not enforce the agent cap, so a
    # brand-new agent is created without the tenant advisory lock.
    monkeypatch.setenv("DEPLOYMENT_MODE", mode)
    for round_no in range(50):
        tenant_id = await _tenant(pg_db)
        user_id = await _user(pg_db, tenant_id)
        await _log(tenant_id, "bot")
        await pg_db.execute(  # a second agent from before the self-hosted cap
            "INSERT INTO agents (agent_id, tenant_id) VALUES ('helper', $1)", tenant_id
        )
        results = await asyncio.gather(
            account.delete_managed_account(user_id=user_id, tenant_id=tenant_id),
            _log(tenant_id, "bot"),
            _log(tenant_id, "helper"),
            _log(tenant_id, f"new-{round_no}"),
            _log(tenant_id, "bot"),
            return_exceptions=True,
        )
        assert not isinstance(results[0], BaseException), repr(results[0])
        for r in results[1:]:
            # A write that waited out the delete finds the account gone (404);
            # on self-hosted a new agent can also be over the cap (409).
            if isinstance(r, HTTPException):
                assert r.status_code == 404 or (mode == "self_hosted" and r.status_code == 409), r
            else:
                assert not isinstance(r, BaseException), repr(r)
        assert await pg_db.fetchval(
            "SELECT COUNT(*) FROM events WHERE tenant_id = $1", tenant_id
        ) == 0


async def test_agent_delete_does_not_deadlock_with_retention(pg_db, pause_retention):
    tenant_id = await _tenant(pg_db)
    await _seed_expired(pg_db, tenant_id, "bot")
    reached, release = pause_retention

    purge = asyncio.create_task(retention.purge_expired(pg_db))
    await asyncio.wait_for(reached.wait(), 5)
    delete = asyncio.create_task(agents_api.delete_agent("bot", tenant={"tenant_id": tenant_id}))
    assert await _still_waiting(delete)

    release.set()
    assert await asyncio.wait_for(purge, 5) == 3
    await asyncio.wait_for(delete, 5)
    assert await pg_db.fetchval("SELECT COUNT(*) FROM agents WHERE tenant_id = $1", tenant_id) == 0


async def test_account_delete_does_not_deadlock_with_retention(pg_db, monkeypatch, pause_retention):
    monkeypatch.setenv("ENV", "production")
    tenant_id = await _tenant(pg_db)
    user_id = await _user(pg_db, tenant_id)
    await _seed_expired(pg_db, tenant_id, "bot")
    reached, release = pause_retention

    purge = asyncio.create_task(retention.purge_expired(pg_db))
    await asyncio.wait_for(reached.wait(), 5)
    delete = asyncio.create_task(
        account.delete_managed_account(user_id=user_id, tenant_id=tenant_id)
    )
    assert await _still_waiting(delete)

    release.set()
    await asyncio.wait_for(purge, 5)
    await asyncio.wait_for(delete, 5)
    assert await pg_db.fetchval("SELECT COUNT(*) FROM tenants WHERE tenant_id = $1", tenant_id) == 0


async def test_account_delete_purges_vectors_after_commit(pg_db, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    tenant_id = await _tenant(pg_db)
    user_id = await _user(pg_db, tenant_id)
    seen = []

    async def purge(tid):
        seen.append(await pg_db.fetchval("SELECT COUNT(*) FROM tenants WHERE tenant_id = $1", tid))

    monkeypatch.setattr(account, "_purge_tenant_vectors", purge)
    await account.delete_managed_account(user_id=user_id, tenant_id=tenant_id)
    assert seen == [0]  # the tenant was already gone when vectors were purged


async def _account_delete_blocked_on_users(pool, tenant_id, user_id):
    """Start an account delete and hold it just after it has taken its locks.

    Holding the users row stops it at ``DELETE FROM users``, after the tenant,
    key and agent locks and before the tenant row. Returns (conn, tx, task).
    """
    conn = await pool.acquire()
    tx = conn.transaction()
    await tx.start()
    await conn.execute("SELECT 1 FROM users WHERE user_id = $1::uuid FOR UPDATE", user_id)
    task = asyncio.create_task(account.delete_managed_account(user_id=user_id, tenant_id=tenant_id))
    assert await _still_waiting(task)
    return conn, tx, task


async def test_key_claimed_during_account_delete_does_not_deadlock(pg_db, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DEPLOYMENT_MODE", "managed")
    tenant_id = await _tenant(pg_db)
    user_id = await _user(pg_db, tenant_id)
    await _log(tenant_id, "bot")
    key = await create_api_key_record(pg_db, tenant_id=tenant_id, name="unassigned")

    conn, tx, delete = await _account_delete_blocked_on_users(pg_db, tenant_id, user_id)
    try:
        # First use of the key binds it: the agent row, then the key row.
        claim = asyncio.create_task(claim_unassigned_api_key(
            key_id=str(key["key_id"]), tenant_id=tenant_id, agent_id="bot", pool=pg_db,
        ))
        assert await _still_waiting(claim)
    finally:
        await tx.commit()
        await pg_db.release(conn)

    await asyncio.wait_for(delete, 5)
    assert await asyncio.wait_for(claim, 5) is None  # the key went with the account


@pytest.mark.parametrize("enforced", ["false", "true"])
async def test_key_created_during_account_delete_does_not_deadlock(pg_db, monkeypatch, enforced):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DEPLOYMENT_MODE", "managed")
    monkeypatch.setenv("API_KEY_LIMITS_ENFORCED", enforced)
    tenant_id = await _tenant(pg_db)
    user_id = await _user(pg_db, tenant_id)
    await _log(tenant_id, "bot")

    conn, tx, delete = await _account_delete_blocked_on_users(pg_db, tenant_id, user_id)
    try:
        create = asyncio.create_task(agents_api.create_agent_api_key(
            "bot", agents_api.CreateAgentKeyBody(), session={"tenant_id": tenant_id},
        ))
        assert await _still_waiting(create)
    finally:
        await tx.commit()
        await pg_db.release(conn)

    await asyncio.wait_for(delete, 5)
    with pytest.raises(HTTPException) as exc:
        await asyncio.wait_for(create, 5)
    assert exc.value.status_code == 404  # the agent went with the account


async def test_forget_racing_account_delete_never_deadlocks(pg_db, monkeypatch):
    """Forget deleting many rows while account delete starts at different
    moments; without the agent lock in delete_and_tombstone, one of these
    deadlocked (tenant row vs event rows)."""
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DEPLOYMENT_MODE", "managed")
    for delay in (0.0, 0.005, 0.01, 0.02, 0.04, 0.08, 0.15):
        tenant_id = await _tenant(pg_db)
        user_id = await _user(pg_db, tenant_id)
        await _log(tenant_id, "bot")
        await pg_db.execute(
            """
            INSERT INTO events (tenant_id, agent_id, event_type, data, checksum, chain_hash)
            SELECT $1, 'bot', 'x', '{"user": "u1"}'::jsonb, md5(g::text), md5(g::text)
            FROM generate_series(1, 2000) g  -- well under #324's lock-table limit
            """,
            tenant_id,
        )

        async def account_delete():
            await asyncio.sleep(delay)
            await account.delete_managed_account(user_id=user_id, tenant_id=tenant_id)

        results = await asyncio.gather(
            forget(ForgetRequest(filter_key="user", filter_value="u1"),
                   tenant={"tenant_id": tenant_id, "user_id": user_id}),
            account_delete(),
            return_exceptions=True,
        )
        for r in results:
            assert not isinstance(r, BaseException), (delay, repr(r))


async def test_write_waiting_on_account_delete_gets_404(pg_db, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DEPLOYMENT_MODE", "managed")
    tenant_id = await _tenant(pg_db)
    user_id = await _user(pg_db, tenant_id)
    await _log(tenant_id, "bot")

    conn, tx, delete = await _account_delete_blocked_on_users(pg_db, tenant_id, user_id)
    try:
        write = asyncio.create_task(_log(tenant_id, "bot"))
        assert await _still_waiting(write)
    finally:
        await tx.commit()
        await pg_db.release(conn)

    await asyncio.wait_for(delete, 5)
    with pytest.raises(HTTPException) as exc:
        await asyncio.wait_for(write, 5)
    assert exc.value.status_code == 404


async def test_agent_delete_and_account_delete_do_not_deadlock_over_keys(pg_db, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DEPLOYMENT_MODE", "managed")
    tenant_id = await _tenant(pg_db)
    user_id = await _user(pg_db, tenant_id)
    await _log(tenant_id, "bot")
    await create_api_key_record(pg_db, tenant_id=tenant_id, name="bound", agent_id="bot")

    # Stop agent delete after it locked the agent, at its DELETE FROM events.
    async with pg_db.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        await conn.execute("SELECT 1 FROM events WHERE tenant_id = $1 FOR UPDATE", tenant_id)
        agent_delete = asyncio.create_task(
            agents_api.delete_agent("bot", tenant={"tenant_id": tenant_id})
        )
        assert await _still_waiting(agent_delete)
        account_delete = asyncio.create_task(
            account.delete_managed_account(user_id=user_id, tenant_id=tenant_id)
        )
        assert await _still_waiting(account_delete)
        await tx.commit()

    # The agent delete cascades to the bound key; the account delete must not
    # be holding it.
    await asyncio.wait_for(agent_delete, 5)
    await asyncio.wait_for(account_delete, 5)
    assert await pg_db.fetchval("SELECT COUNT(*) FROM tenants WHERE tenant_id = $1", tenant_id) == 0


async def test_key_claim_racing_agent_delete_recreates_the_agent(pg_db, monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "managed")
    tenant_id = await _tenant(pg_db)
    await pg_db.execute("INSERT INTO agents (agent_id, tenant_id) VALUES ('bot', $1)", tenant_id)
    key = await create_api_key_record(pg_db, tenant_id=tenant_id, name="unassigned")

    async with pg_db.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        await conn.execute(
            "SELECT 1 FROM agents WHERE tenant_id = $1 AND agent_id = 'bot' FOR UPDATE", tenant_id
        )
        claim = asyncio.create_task(claim_unassigned_api_key(
            key_id=str(key["key_id"]), tenant_id=tenant_id, agent_id="bot", pool=pg_db,
        ))
        assert await _still_waiting(claim)
        await conn.execute("DELETE FROM agents WHERE tenant_id = $1 AND agent_id = 'bot'", tenant_id)
        await tx.commit()

    assert await asyncio.wait_for(claim, 5) == "bot"
    assert await pg_db.fetchval(
        "SELECT agent_id FROM api_keys WHERE key_id = $1", key["key_id"]
    ) == "bot"


async def test_key_created_and_claimed_during_account_delete_does_not_deadlock(pg_db, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DEPLOYMENT_MODE", "managed")
    tenant_id = await _tenant(pg_db)
    user_id = await _user(pg_db, tenant_id)
    await _log(tenant_id, "bot")

    conn, tx, delete = await _account_delete_blocked_on_users(pg_db, tenant_id, user_id)
    try:
        # A key created after the delete took its locks, then used.
        key = await asyncio.wait_for(
            create_api_key_record(pg_db, tenant_id=tenant_id, name="late"), 5
        )
        claim = asyncio.create_task(claim_unassigned_api_key(
            key_id=str(key["key_id"]), tenant_id=tenant_id, agent_id="bot", pool=pg_db,
        ))
        assert await _still_waiting(claim)
    finally:
        await tx.commit()
        await pg_db.release(conn)

    await asyncio.wait_for(delete, 5)
    assert await asyncio.wait_for(claim, 5) is None


async def test_key_for_a_missing_agent_is_404_even_with_the_key_cap_full(pg_db):
    tenant_id = await _tenant(pg_db)  # self-hosted: one key
    await _log(tenant_id, "bot")
    await create_api_key_record(pg_db, tenant_id=tenant_id, name="the one key", agent_id="bot")

    with pytest.raises(HTTPException) as exc:
        await agents_api.create_agent_api_key(
            "typo", agents_api.CreateAgentKeyBody(), session={"tenant_id": tenant_id},
        )
    assert exc.value.status_code == 404


@pytest.mark.parametrize("agent", ["newbot", "support-bot"])
async def test_key_claimed_for_a_new_agent_during_account_delete(pg_db, monkeypatch, agent):
    """Creating the agent waits on the tenant lock even where no cap applies."""
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DEPLOYMENT_MODE", "managed")
    tenant_id = await _tenant(pg_db)
    user_id = await _user(pg_db, tenant_id)
    await _log(tenant_id, "bot")
    key = await create_api_key_record(pg_db, tenant_id=tenant_id, name="unassigned")

    conn, tx, delete = await _account_delete_blocked_on_users(pg_db, tenant_id, user_id)
    try:
        claim = asyncio.create_task(claim_unassigned_api_key(
            key_id=str(key["key_id"]), tenant_id=tenant_id, agent_id=agent, pool=pg_db,
        ))
        assert await _still_waiting(claim)
    finally:
        await tx.commit()
        await pg_db.release(conn)

    await asyncio.wait_for(delete, 5)
    assert await asyncio.wait_for(claim, 5) is None


async def test_losing_claim_does_not_leave_the_agent_it_created(pg_db, monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "managed")
    tenant_id = await _tenant(pg_db)
    key = await create_api_key_record(pg_db, tenant_id=tenant_id, name="unassigned")
    assert await claim_unassigned_api_key(
        key_id=str(key["key_id"]), tenant_id=tenant_id, agent_id="a", pool=pg_db,
    ) == "a"

    # A second first-use request named another agent: it sees the winner and
    # rolls back the agent it had created.
    assert await claim_unassigned_api_key(
        key_id=str(key["key_id"]), tenant_id=tenant_id, agent_id="b", pool=pg_db,
    ) == "a"
    assert await pg_db.fetchval(
        "SELECT array_agg(agent_id) FROM agents WHERE tenant_id = $1", tenant_id
    ) == ["a"]
