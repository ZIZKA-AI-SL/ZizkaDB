"""Per-agent hash chain: detects removed, inserted, reordered and edited events."""

import asyncio
import json

import pytest

from api.memory import ForgetRequest, forget
from services.audit_chain import compute_chain_hash, verify_chain
from services.checksum import CHECKSUM_COLUMNS, compute_row_checksum_v2
from services.event_write import write_event

pytestmark = pytest.mark.postgres

AGENT = "bot"


async def _setup(pool, n=5) -> tuple[str, list[dict]]:
    tenant_id = str(await pool.fetchval(
        "INSERT INTO tenants (name) VALUES ('acme') RETURNING tenant_id"
    ))
    written = [
        await write_event(tenant_id=tenant_id, agent=AGENT, event="step",
                          data={"i": i, "user": f"u{i}"})
        for i in range(n)
    ]
    return tenant_id, written


async def test_fresh_chain_is_intact_and_head_is_last_event(pg_db):
    tenant_id, written = await _setup(pg_db)
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True
    assert result["summary"]["chained"] == 5
    assert result["chain_head"] == {
        "sequence_no": written[-1]["sequence_no"],
        "chain_hash": written[-1]["chain_hash"],
    }


async def test_edited_row_is_detected(pg_db):
    tenant_id, written = await _setup(pg_db)
    await pg_db.execute(
        "UPDATE events SET data = '{\"i\": 99}' WHERE event_id = $1", written[2]["event_id"]
    )
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is False
    assert result["problems"][0]["problem"] == "checksum_mismatch"


async def test_silently_deleted_row_breaks_the_chain(pg_db):
    tenant_id, written = await _setup(pg_db)
    await pg_db.execute("DELETE FROM events WHERE event_id = $1", written[2]["event_id"])
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is False
    assert result["problems"][0]["problem"] == "chain_break"
    assert result["problems"][0]["sequence_no"] == written[3]["sequence_no"]


async def test_recomputed_checksum_still_breaks_the_chain(pg_db):
    """Swapping a row's content and checksum together is caught by the next link."""
    tenant_id, written = await _setup(pg_db)
    await pg_db.execute(
        "UPDATE events SET chain_hash = repeat('0', 64) WHERE event_id = $1",
        written[1]["event_id"],
    )
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert {p["sequence_no"] for p in result["problems"]} == {
        written[1]["sequence_no"], written[2]["sequence_no"]
    }


async def test_forget_leaves_tombstones_and_the_chain_verifies(pg_db):
    tenant_id, written = await _setup(pg_db)
    await forget(ForgetRequest(filter_key="user", filter_value="u2"),
                 tenant={"tenant_id": tenant_id, "agent_id": AGENT})
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True
    assert result["summary"]["tombstones"] == 1
    assert result["summary"]["events"] == 4
    row = await pg_db.fetchrow("SELECT * FROM event_tombstones")
    assert row["reason"] == "forget" and row["chain_hash"] == written[2]["chain_hash"]

    # Writing after a forget of the newest event continues from its tombstone.
    await forget(ForgetRequest(filter_key="user", filter_value="u4"),
                 tenant={"tenant_id": tenant_id, "agent_id": AGENT})
    await write_event(tenant_id=tenant_id, agent=AGENT, event="step", data={"i": 5})
    assert (await verify_chain(pg_db, tenant_id, AGENT))["intact"] is True


async def test_legacy_rows_before_the_chain_are_allowed(pg_db):
    tenant_id = str(await pg_db.fetchval(
        "INSERT INTO tenants (name) VALUES ('acme') RETURNING tenant_id"
    ))
    await pg_db.execute("INSERT INTO agents (agent_id, tenant_id) VALUES ($1, $2)", AGENT, tenant_id)
    await pg_db.execute(
        "INSERT INTO events (tenant_id, agent_id, event_type, data, checksum) VALUES ($1, $2, 'old', $3, 'x')",
        tenant_id, AGENT, json.dumps({}),
    )
    await write_event(tenant_id=tenant_id, agent=AGENT, event="new", data={})
    result = await verify_chain(pg_db, tenant_id, AGENT, legacy_until_seq=10**9)
    assert result["summary"]["unchained_legacy"] == 1
    assert result["summary"]["breaks"] == 0


async def test_row_inserted_behind_the_write_path_is_detected(pg_db):
    tenant_id, _ = await _setup(pg_db, n=2)
    await pg_db.execute(
        "INSERT INTO events (tenant_id, agent_id, event_type, data, checksum) VALUES ($1, $2, 'sneaky', '{}', 'x')",
        tenant_id, AGENT,
    )
    problems = (await verify_chain(pg_db, tenant_id, AGENT))["problems"]
    assert "unchained_after_chain_start" in {p["problem"] for p in problems}


async def test_resume_from_an_anchor(pg_db):
    tenant_id, written = await _setup(pg_db, n=3)
    head = (await verify_chain(pg_db, tenant_id, AGENT))["chain_head"]
    await write_event(tenant_id=tenant_id, agent=AGENT, event="later", data={})

    ok = await verify_chain(pg_db, tenant_id, AGENT,
                            after_seq=head["sequence_no"], anchor=head["chain_hash"])
    assert ok["intact"] is True and ok["summary"]["events"] == 1

    bad = await verify_chain(pg_db, tenant_id, AGENT,
                             after_seq=head["sequence_no"], anchor="f" * 64)
    assert bad["intact"] is False
    assert bad["problems"][0]["problem"] == "anchor_mismatch"


async def test_concurrent_writes_keep_one_linear_chain(pg_db):
    tenant_id, _ = await _setup(pg_db, n=1)
    await asyncio.gather(*[
        write_event(tenant_id=tenant_id, agent=AGENT, event="burst", data={"n": n})
        for n in range(20)
    ])
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True and result["summary"]["chained"] == 21


async def _forget(tenant_id: str, value: str) -> dict:
    return await forget(ForgetRequest(filter_key="user", filter_value=value),
                        tenant={"tenant_id": tenant_id, "agent_id": AGENT})


async def _rewrite_event(pool, event_id, data: dict, prev_chain_hash: str) -> None:
    """Edit an event and recompute its checksum and its own link, as a careful
    database writer would; only the links after it are left alone."""
    await pool.execute("UPDATE events SET data = $2 WHERE event_id = $1", event_id, json.dumps(data))
    row = await pool.fetchrow(f"SELECT {CHECKSUM_COLUMNS} FROM events WHERE event_id = $1", event_id)
    checksum = compute_row_checksum_v2(row)
    await pool.execute(
        "UPDATE events SET checksum = $2, chain_hash = $3 WHERE event_id = $1",
        event_id, checksum, compute_chain_hash(prev_chain_hash, checksum),
    )


async def test_tombstones_keep_the_deleted_rows_checksum(pg_db):
    tenant_id, written = await _setup(pg_db)
    await _forget(tenant_id, "u2")
    row = await pg_db.fetchrow("SELECT checksum, chain_hash FROM event_tombstones")
    assert row["checksum"] == written[2]["checksum"]
    assert row["chain_hash"] == compute_chain_hash(written[1]["chain_hash"], row["checksum"])


async def test_edit_just_before_a_tombstone_is_detected(pg_db):
    """The chain must not restart at an erasure (ADR-009 H1)."""
    tenant_id, written = await _setup(pg_db)
    await _forget(tenant_id, "u2")
    await _rewrite_event(pg_db, written[1]["event_id"], {"i": 1, "user": "forged"},
                         written[0]["chain_hash"])

    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is False
    assert result["problems"][0]["problem"] == "chain_break"
    assert result["problems"][0]["sequence_no"] == written[2]["sequence_no"]


async def test_tampered_tombstone_checksum_is_detected(pg_db):
    tenant_id, written = await _setup(pg_db)
    await _forget(tenant_id, "u2")
    await pg_db.execute("UPDATE event_tombstones SET checksum = repeat('a', 64)")

    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is False
    assert result["problems"][0]["problem"] == "chain_break"
    assert result["problems"][0]["sequence_no"] == written[2]["sequence_no"]


async def _old_style_forget(pool, tenant_id: str, event_id) -> None:
    """A 0.5.0 forget: tombstone (no checksum given) first, then the delete."""
    await pool.execute(
        """
        INSERT INTO event_tombstones (tenant_id, agent_id, event_id, sequence_no, chain_hash, reason)
        SELECT tenant_id, agent_id, event_id, sequence_no, chain_hash, 'forget'
        FROM events WHERE event_id = $1
        """,
        event_id,
    )
    await pool.execute("DELETE FROM events WHERE event_id = $1", event_id)


async def _pre_upgrade_tombstone(pool, tenant_id: str, event_id) -> None:
    """A tombstone as 0.5.0 left it before the upgrade: no checksum, deleted
    before the cutoff init_db() recorded."""
    await _old_style_forget(pool, tenant_id, event_id)
    await pool.execute(
        """
        UPDATE event_tombstones
        SET checksum = NULL,
            deleted_at = (SELECT since FROM tombstone_checksum_cutoff) - interval '1 day'
        WHERE event_id = $1
        """,
        event_id,
    )


async def test_tombstones_from_before_the_upgrade_still_verify(pg_db):
    tenant_id, written = await _setup(pg_db)
    await _pre_upgrade_tombstone(pg_db, tenant_id, written[1]["event_id"])
    await _forget(tenant_id, "u3")

    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True
    assert result["summary"]["legacy_tombstones"] == 1
    assert result["summary"]["tombstones"] == 2


async def test_a_checksum_blanked_after_the_cutoff_is_detected(pg_db):
    """Blanking a new tombstone's checksum must not turn it into a legacy one."""
    tenant_id, written = await _setup(pg_db)
    await _forget(tenant_id, "u2")
    await pg_db.execute("UPDATE event_tombstones SET checksum = NULL")

    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is False
    assert result["problems"][0]["problem"] == "tombstone_missing_checksum"
    assert result["summary"]["legacy_tombstones"] == 0


async def test_an_older_image_erasing_after_the_upgrade_still_verifies(pg_db):
    """Rolling deploy or downgrade: the older image's tombstones get their
    checksum from the trigger, so they are checked, not reported."""
    tenant_id, written = await _setup(pg_db)
    await _forget(tenant_id, "u2")
    await _old_style_forget(pg_db, tenant_id, written[4]["event_id"])

    row = await pg_db.fetchrow(
        "SELECT checksum FROM event_tombstones WHERE event_id = $1", written[4]["event_id"]
    )
    assert row["checksum"] == written[4]["checksum"]
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True and result["summary"]["legacy_tombstones"] == 0


async def test_startup_keeps_one_trigger_and_the_first_cutoff(pg_db):
    import db.connection as connection

    since = await pg_db.fetchval("SELECT since FROM tombstone_checksum_cutoff")
    await connection._run_migrations()
    await connection._run_migrations()
    assert await pg_db.fetchval("SELECT since FROM tombstone_checksum_cutoff") == since
    assert await pg_db.fetchval(
        "SELECT COUNT(*) FROM pg_trigger WHERE tgname = 'trg_tombstone_checksum'"
    ) == 1


async def test_overlapping_forgets_count_each_row_once(pg_db, monkeypatch):
    """Both forgets select the same rows before either deletes them (ADR-009 H2)."""
    tenant_id = str(await pg_db.fetchval(
        "INSERT INTO tenants (name) VALUES ('acme') RETURNING tenant_id"
    ))
    for i in range(3):
        await write_event(tenant_id=tenant_id, agent=AGENT, event="step",
                          data={"i": i, "user": "shared"})

    gate = asyncio.Event()

    async def held_vector_delete(event_ids, tenant):
        await gate.wait()
        return True

    monkeypatch.setattr("api.memory._delete_vectors", held_vector_delete)
    first = asyncio.create_task(_forget(tenant_id, "shared"))
    await _wait_until(lambda: _advisory_locks(pg_db, granted=True), 3)   # first holds them
    second = asyncio.create_task(_forget(tenant_id, "shared"))
    await _wait_until(lambda: _advisory_locks(pg_db, granted=False), 1)  # second selected, now waits
    gate.set()
    results = await asyncio.gather(first, second)

    assert sorted(r["deleted_events"] for r in results) == [0, 3]
    assert await pg_db.fetchval("SELECT COUNT(*) FROM event_tombstones") == 3
    assert (await verify_chain(pg_db, tenant_id, AGENT))["intact"] is True


async def _advisory_locks(pool, *, granted: bool) -> int:
    """Advisory locks held (or awaited) in this test's own database only."""
    return await pool.fetchval(
        """
        SELECT COUNT(*) FROM pg_locks
        WHERE locktype = 'advisory' AND granted = $1
          AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
        """,
        granted,
    )


async def _wait_until(count, at_least: int, timeout: float = 10.0) -> None:
    """Poll until count() >= at_least, so a test proves the overlap it relies on."""
    deadline = asyncio.get_running_loop().time() + timeout
    while await count() < at_least:
        assert asyncio.get_running_loop().time() < deadline, "the expected overlap never happened"
        await asyncio.sleep(0.02)


async def test_retention_waits_for_a_forget_instead_of_deadlocking(pg_db, monkeypatch):
    """Retention takes the per-event locks before any row lock, like forget,
    so a forget holding them makes retention wait rather than deadlock."""
    from datetime import datetime, timedelta, timezone

    from services import retention

    tenant_id = str(await pg_db.fetchval(
        "INSERT INTO tenants (name) VALUES ('acme') RETURNING tenant_id"
    ))
    old = datetime.now(timezone.utc) - timedelta(days=40)
    for i in range(3):
        await write_event(tenant_id=tenant_id, agent=AGENT, event="step",
                          data={"i": i, "user": "shared" if i < 2 else "other"}, occurred_at=old)

    gate = asyncio.Event()

    async def held_vector_delete(event_ids, tenant):
        await gate.wait()
        return True

    monkeypatch.setattr("api.memory._delete_vectors", held_vector_delete)
    monkeypatch.setenv("EVENT_RETENTION_DAYS", "30")
    forgetting = asyncio.create_task(_forget(tenant_id, "shared"))
    await _wait_until(lambda: _advisory_locks(pg_db, granted=True), 2)
    purging = asyncio.create_task(retention.run_once(pg_db))
    await _wait_until(lambda: _advisory_locks(pg_db, granted=False), 1)
    gate.set()
    forgot, purged = await asyncio.gather(forgetting, purging)

    assert forgot["deleted_events"] == 2
    assert purged == 1
    assert await pg_db.fetchval("SELECT COUNT(*) FROM events WHERE tenant_id = $1", tenant_id) == 0
    assert (await verify_chain(pg_db, tenant_id, AGENT))["intact"] is True


async def test_upgrade_from_0_5_0_adds_the_trigger_and_keeps_old_tombstones(pg_db):
    """The real upgrade path: a database in its 0.5.0 state, then the migrations."""
    import db.connection as connection

    tenant_id, written = await _setup(pg_db)
    fresh_body = await pg_db.fetchval(
        "SELECT prosrc FROM pg_proc WHERE proname = 'zizkadb_tombstone_checksum'"
    )   # as schema.sql created it
    await pg_db.execute("""
        DROP TRIGGER trg_tombstone_checksum ON event_tombstones;
        DROP FUNCTION zizkadb_tombstone_checksum();
        DROP TABLE tombstone_checksum_cutoff;
        ALTER TABLE event_tombstones DROP COLUMN checksum;
    """)
    await _old_style_forget(pg_db, tenant_id, written[1]["event_id"])   # erased under 0.5.0

    await connection._run_migrations()

    assert await pg_db.fetchval(
        "SELECT COUNT(*) FROM pg_trigger WHERE tgname = 'trg_tombstone_checksum'"
    ) == 1
    upgraded_body = await pg_db.fetchval(
        "SELECT prosrc FROM pg_proc WHERE proname = 'zizkadb_tombstone_checksum'"
    )   # as init_db() created it
    # schema.sql and init_db() carry a copy each; fresh and upgraded installs
    # must run the same trigger.
    assert " ".join(upgraded_body.split()) == " ".join(fresh_body.split())
    assert await pg_db.fetchval("SELECT checksum FROM event_tombstones") is None
    await _old_style_forget(pg_db, tenant_id, written[3]["event_id"])   # 0.5.0 still running
    await _forget(tenant_id, "u4")                                      # new image
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True, result["problems"]
    assert result["summary"]["legacy_tombstones"] == 1
    assert result["summary"]["tombstones"] == 3


async def test_erasure_locks_use_the_embed_workers_key(pg_db):
    """forget and retention lock hashtext(event_id), the key the embed worker takes."""
    from services.audit_chain import lock_events

    tenant_id, written = await _setup(pg_db, n=2)
    ids = [w["event_id"] for w in written]
    async with pg_db.acquire() as holder, holder.transaction():
        await lock_events(holder, [*ids, ids[0]])         # duplicates are fine
        async with pg_db.acquire() as other:
            for eid in ids:
                assert await other.fetchval(
                    "SELECT pg_try_advisory_lock(hashtext($1))", str(eid)
                ) is False


async def test_erasure_lock_order_matches_0_5_0(pg_db):
    """lock_events orders ids byte-wise, exactly like 0.5.0's sorted(ids, key=str),
    so an older and a newer image erasing the same events lock in one order."""
    import uuid

    ids = [uuid.uuid4() for _ in range(500)]
    ordered = await pg_db.fetchval(
        """
        SELECT array_agg(id ORDER BY id)
        FROM (SELECT DISTINCT e::text COLLATE "C" AS id FROM unnest($1::uuid[]) AS e) ids
        """,
        ids,
    )
    assert ordered == sorted(str(i) for i in ids)


# ── Stored head (ADR-009 H5) ──────────────────────────────────────────────────

async def _stored_head(pool, tenant_id: str) -> tuple:
    """What the verifier reads as the head."""
    from services.audit_chain import _stored_head as read_head

    head = await read_head(pool, tenant_id, AGENT)
    return head[:2]   # (seq, hash); the third value is first_seen


async def test_each_write_records_the_agents_newest_link(pg_db):
    tenant_id, written = await _setup(pg_db)
    assert await _stored_head(pg_db, tenant_id) == (written[-1]["sequence_no"], written[-1]["chain_hash"])


async def test_an_idempotent_duplicate_does_not_move_the_head(pg_db):
    tenant_id, _ = await _setup(pg_db, n=1)
    await write_event(tenant_id=tenant_id, agent=AGENT, event="once", data={}, idempotency_key="k1")
    latest = await write_event(tenant_id=tenant_id, agent=AGENT, event="next", data={})
    repeat = await write_event(tenant_id=tenant_id, agent=AGENT, event="once", data={}, idempotency_key="k1")
    assert repeat["duplicate"] is True
    assert await _stored_head(pg_db, tenant_id) == (latest["sequence_no"], latest["chain_hash"])


async def test_deleting_the_newest_event_is_detected(pg_db):
    """Nothing links after the newest event, so only the stored head shows it went missing."""
    tenant_id, written = await _setup(pg_db)
    await pg_db.execute("DELETE FROM events WHERE event_id = $1", written[-1]["event_id"])

    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is False
    assert [p["problem"] for p in result["problems"]] == ["head_mismatch"]
    assert result["problems"][0]["sequence_no"] == written[-1]["sequence_no"]


async def test_editing_the_newest_event_with_its_own_link_recomputed_is_detected(pg_db):
    tenant_id, written = await _setup(pg_db)
    await _rewrite_event(pg_db, written[-1]["event_id"], {"i": 4, "user": "forged"},
                         written[-2]["chain_hash"])

    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is False
    assert [p["problem"] for p in result["problems"]] == ["head_mismatch"]


async def test_forgetting_the_newest_event_keeps_the_head_valid(pg_db):
    tenant_id, written = await _setup(pg_db)
    await _forget(tenant_id, "u4")
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True and result["summary"]["stale_head"] == 0


async def test_a_head_an_older_image_left_behind_is_stale_not_broken(pg_db):
    """After a downgrade, the older image writes without moving the head."""
    tenant_id, written = await _setup(pg_db)
    await pg_db.execute(
        "UPDATE agents SET chain_head_seq = $2, chain_head_hash = $3 WHERE tenant_id = $1",
        tenant_id, written[2]["sequence_no"], written[2]["chain_hash"],
    )
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True and result["summary"]["stale_head"] == 1

    # The next write on this version records the head again.
    latest = await write_event(tenant_id=tenant_id, agent=AGENT, event="later", data={})
    assert await _stored_head(pg_db, tenant_id) == (latest["sequence_no"], latest["chain_hash"])
    assert (await verify_chain(pg_db, tenant_id, AGENT))["summary"]["stale_head"] == 0


async def test_a_head_with_a_different_hash_is_a_mismatch(pg_db):
    tenant_id, written = await _setup(pg_db)
    await pg_db.execute(
        "UPDATE agents SET chain_head_seq = $2, chain_head_hash = repeat('e', 64) WHERE tenant_id = $1",
        tenant_id, written[2]["sequence_no"],
    )
    problems = (await verify_chain(pg_db, tenant_id, AGENT))["problems"]
    assert [p["problem"] for p in problems] == ["head_mismatch"]


async def test_an_agent_without_a_recorded_head_still_verifies(pg_db):
    """Agents from before the upgrade have no head until their next write."""
    tenant_id, _ = await _setup(pg_db)
    await pg_db.execute(
        "UPDATE agents SET chain_head_seq = NULL, chain_head_hash = NULL WHERE tenant_id = $1", tenant_id
    )
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True and result["summary"]["stale_head"] == 0


async def test_a_deleted_tail_is_caught_when_resuming_from_an_anchor(pg_db):
    tenant_id, written = await _setup(pg_db)
    await pg_db.execute("DELETE FROM events WHERE event_id = $1", written[-1]["event_id"])

    resumed = await verify_chain(pg_db, tenant_id, AGENT, after_seq=written[1]["sequence_no"],
                                 anchor=written[1]["chain_hash"])
    assert resumed["intact"] is False
    assert [p["problem"] for p in resumed["problems"]] == ["head_mismatch"]


async def test_a_wrong_anchor_is_reported_once_not_as_a_missing_tail(pg_db):
    tenant_id, written = await _setup(pg_db, n=3)
    result = await verify_chain(pg_db, tenant_id, AGENT, after_seq=written[-1]["sequence_no"],
                                anchor="f" * 64)
    assert [p["problem"] for p in result["problems"]] == ["anchor_mismatch"]


class _PoolThatActsDuringTheCheck:
    """Runs ``action`` once: when the walk asks for its first page of rows, or
    with ``after_walk``, just before the head is read the second time."""

    def __init__(self, pool, action, *, after_walk=False):
        self._pool, self._action, self._after_walk = pool, action, after_walk
        self._done, self._fetchrows = False, 0

    async def _maybe_act(self):
        if not self._done:
            self._done = True
            await self._action()

    async def fetch(self, *args):
        if not self._after_walk:
            await self._maybe_act()
        return await self._pool.fetch(*args)

    async def fetchrow(self, *args):
        self._fetchrows += 1
        if self._after_walk and self._fetchrows == 2:
            await self._maybe_act()
        return await self._pool.fetchrow(*args)

    def __getattr__(self, name):
        return getattr(self._pool, name)


async def test_a_write_during_the_check_is_not_a_missing_tail(pg_db):
    tenant_id, _ = await _setup(pg_db)

    async def write():
        await write_event(tenant_id=tenant_id, agent=AGENT, event="meanwhile", data={})

    for after_walk in (False, True):
        pool = _PoolThatActsDuringTheCheck(pg_db, write, after_walk=after_walk)
        result = await verify_chain(pool, tenant_id, AGENT)
        assert result["intact"] is True, (after_walk, result["problems"])
        # Skipped because the head changed, not passed off as an older image's head.
        assert result["summary"]["stale_head"] == 0, after_walk


async def test_an_agent_deleted_during_the_check_is_not_a_missing_tail(pg_db):
    tenant_id, _ = await _setup(pg_db)

    async def delete_agent():
        await pg_db.execute("DELETE FROM events WHERE tenant_id = $1", tenant_id)
        await pg_db.execute("DELETE FROM agents WHERE tenant_id = $1", tenant_id)

    result = await verify_chain(_PoolThatActsDuringTheCheck(pg_db, delete_agent), tenant_id, AGENT)
    assert "head_mismatch" not in {p["problem"] for p in result["problems"]}


async def test_racing_duplicates_record_the_head_of_the_one_insert(pg_db):
    tenant_id, _ = await _setup(pg_db, n=1)
    results = await asyncio.gather(*[
        write_event(tenant_id=tenant_id, agent=AGENT, event="once", data={}, idempotency_key="race")
        for _ in range(5)
    ])
    inserted = [r for r in results if not r.get("duplicate")]
    assert len(inserted) == 1
    assert await _stored_head(pg_db, tenant_id) == (inserted[0]["sequence_no"], inserted[0]["chain_hash"])
    assert (await verify_chain(pg_db, tenant_id, AGENT))["intact"] is True


async def test_concurrent_writers_leave_the_head_on_the_last_link(pg_db):
    tenant_id, _ = await _setup(pg_db, n=1)
    await asyncio.gather(*[
        write_event(tenant_id=tenant_id, agent=AGENT, event="burst", data={"n": n})
        for n in range(20)
    ])
    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is True and result["summary"]["stale_head"] == 0
    assert await _stored_head(pg_db, tenant_id) == (
        result["chain_head"]["sequence_no"], result["chain_head"]["chain_hash"]
    )


async def test_upgrade_adds_the_head_columns_once(pg_db):
    import db.connection as connection

    await pg_db.execute("DROP TABLE chain_head_breaks")
    await pg_db.execute("ALTER TABLE agents DROP COLUMN chain_head_seq, DROP COLUMN chain_head_hash")
    await connection._run_migrations()
    assert await pg_db.fetchval("SELECT to_regclass('chain_head_breaks') IS NOT NULL")
    tenant_id, written = await _setup(pg_db, n=2)
    assert await _stored_head(pg_db, tenant_id) == (written[-1]["sequence_no"], written[-1]["chain_hash"])

    # A normal start takes no lock on agents: it completes while another
    # session holds the table, which an ALTER would have to wait for.
    async with pg_db.acquire() as reader, reader.transaction():
        await reader.execute("LOCK TABLE agents IN ACCESS SHARE MODE")
        await asyncio.wait_for(connection._run_migrations(), timeout=20)


async def test_a_locked_table_fails_the_upgrade_clearly_and_adds_nothing(pg_db, monkeypatch):
    import db.connection as connection

    monkeypatch.setattr(connection, "ADD_COLUMN_LOCK_TIMEOUT", "100ms")
    monkeypatch.setattr(connection, "ADD_COLUMN_ATTEMPTS", 2)
    monkeypatch.setattr(connection, "ADD_COLUMN_RETRY_DELAY", 0)
    await pg_db.execute("ALTER TABLE agents DROP COLUMN chain_head_seq, DROP COLUMN chain_head_hash")

    async with pg_db.acquire() as reader, reader.transaction():
        await reader.execute("LOCK TABLE agents IN ACCESS SHARE MODE")
        with pytest.raises(RuntimeError, match="Could not add chain_head_seq, chain_head_hash to agents"):
            await connection._add_columns_once(
                "agents", [("chain_head_seq", "BIGINT"), ("chain_head_hash", "VARCHAR(64)")]
            )
    columns = {r["column_name"] for r in await pg_db.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'agents'"
    )}
    assert not {"chain_head_seq", "chain_head_hash"} & columns   # never half done


# ── A tail removed before the next write stays reported ───────────────────────

async def test_a_deleted_tail_stays_reported_after_the_next_write(pg_db):
    """The next event links past the gap, so the write keeps the evidence."""
    tenant_id, written = await _setup(pg_db)
    await pg_db.execute("DELETE FROM events WHERE event_id = $1", written[-1]["event_id"])
    await write_event(tenant_id=tenant_id, agent=AGENT, event="later", data={})

    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is False
    assert [p["problem"] for p in result["problems"]] == ["head_mismatch"]
    assert result["problems"][0]["sequence_no"] == written[-1]["sequence_no"]
    assert result["summary"]["recorded_head_breaks"] == 1


async def test_an_edited_newest_event_stays_reported_after_the_next_write(pg_db):
    tenant_id, written = await _setup(pg_db)
    await _rewrite_event(pg_db, written[-1]["event_id"], {"i": 4, "user": "forged"},
                         written[-2]["chain_hash"])
    await write_event(tenant_id=tenant_id, agent=AGENT, event="later", data={})

    result = await verify_chain(pg_db, tenant_id, AGENT)
    assert result["intact"] is False
    assert result["summary"]["recorded_head_breaks"] == 1


async def test_writing_after_an_older_images_writes_records_nothing(pg_db):
    tenant_id, written = await _setup(pg_db)
    await pg_db.execute(   # what an older image leaves: the head on an older link
        "UPDATE agents SET chain_head_seq = $2, chain_head_hash = $3 WHERE tenant_id = $1",
        tenant_id, written[2]["sequence_no"], written[2]["chain_hash"],
    )
    await write_event(tenant_id=tenant_id, agent=AGENT, event="later", data={})
    assert await pg_db.fetchval("SELECT COUNT(*) FROM chain_head_breaks") == 0
    assert (await verify_chain(pg_db, tenant_id, AGENT))["intact"] is True


async def test_writing_after_forgetting_the_newest_event_records_nothing(pg_db):
    tenant_id, _ = await _setup(pg_db)
    await _forget(tenant_id, "u4")
    await write_event(tenant_id=tenant_id, agent=AGENT, event="later", data={})
    assert await pg_db.fetchval("SELECT COUNT(*) FROM chain_head_breaks") == 0
    assert (await verify_chain(pg_db, tenant_id, AGENT))["intact"] is True


async def test_the_summary_says_whether_the_head_was_checked(pg_db):
    tenant_id, _ = await _setup(pg_db)
    assert (await verify_chain(pg_db, tenant_id, AGENT))["summary"]["head_checked"] == 1
    await pg_db.execute(
        "UPDATE agents SET chain_head_seq = NULL, chain_head_hash = NULL WHERE tenant_id = $1", tenant_id
    )
    assert (await verify_chain(pg_db, tenant_id, AGENT))["summary"]["head_checked"] == 0


async def test_a_wrong_anchor_does_not_hide_a_deleted_tail(pg_db):
    tenant_id, written = await _setup(pg_db)
    await pg_db.execute("DELETE FROM events WHERE event_id = $1", written[-1]["event_id"])
    result = await verify_chain(pg_db, tenant_id, AGENT, after_seq=written[-2]["sequence_no"],
                                anchor="f" * 64)
    assert [p["problem"] for p in result["problems"]] == ["anchor_mismatch", "head_mismatch"]


async def test_a_deleted_tail_is_caught_even_if_the_head_moves_during_the_check(pg_db):
    """The head read before the walk is still checked when it changes meanwhile."""
    tenant_id, written = await _setup(pg_db)
    await pg_db.execute("DELETE FROM events WHERE event_id = $1", written[-1]["event_id"])

    async def move_head():   # moves the head without recording a break
        await pg_db.execute(
            "UPDATE agents SET chain_head_seq = $2, chain_head_hash = $3 WHERE tenant_id = $1",
            tenant_id, written[-2]["sequence_no"], written[-2]["chain_hash"],
        )

    result = await verify_chain(_PoolThatActsDuringTheCheck(pg_db, move_head), tenant_id, AGENT)
    assert result["intact"] is False
    assert [p["problem"] for p in result["problems"]] == ["head_mismatch"]
    assert result["summary"]["recorded_head_breaks"] == 0


async def test_one_deleted_tail_is_counted_once(pg_db):
    """Seen by the live check and recorded by a write during the check: one problem."""
    tenant_id, written = await _setup(pg_db)
    await pg_db.execute("DELETE FROM events WHERE event_id = $1", written[-1]["event_id"])

    async def write():
        await write_event(tenant_id=tenant_id, agent=AGENT, event="meanwhile", data={})

    result = await verify_chain(_PoolThatActsDuringTheCheck(pg_db, write), tenant_id, AGENT)
    assert [p["problem"] for p in result["problems"]] == ["head_mismatch"]
    assert result["summary"]["breaks"] == 1


async def test_rows_inserted_outside_the_write_path_do_not_record_a_break(pg_db):
    """Seed scripts insert rows without a link; the next write links past them."""
    tenant_id, _ = await _setup(pg_db)
    await pg_db.execute(
        "INSERT INTO events (tenant_id, agent_id, event_type, data, checksum) VALUES ($1, $2, 'seeded', '{}', 'x')",
        tenant_id, AGENT,
    )
    await write_event(tenant_id=tenant_id, agent=AGENT, event="later", data={})
    assert await pg_db.fetchval("SELECT COUNT(*) FROM chain_head_breaks") == 0
    kinds = {p["problem"] for p in (await verify_chain(pg_db, tenant_id, AGENT))["problems"]}
    # The seeded row itself is reported (no link; its fake checksum), never as a missing tail.
    assert "unchained_after_chain_start" in kinds and "head_mismatch" not in kinds


async def test_resuming_past_a_recorded_incident_comes_back_clean(pg_db):
    tenant_id, written = await _setup(pg_db)
    await pg_db.execute("DELETE FROM events WHERE event_id = $1", written[-1]["event_id"])
    await write_event(tenant_id=tenant_id, agent=AGENT, event="later", data={})
    seen = await verify_chain(pg_db, tenant_id, AGENT)
    assert seen["summary"]["recorded_head_breaks"] == 1

    head = seen["chain_head"]          # the auditor stores it after reviewing the incident
    await write_event(tenant_id=tenant_id, agent=AGENT, event="next", data={})
    resumed = await verify_chain(pg_db, tenant_id, AGENT,
                                 after_seq=head["sequence_no"], anchor=head["chain_hash"])
    assert resumed["intact"] is True and resumed["summary"]["recorded_head_breaks"] == 0


async def test_recorded_head_breaks_go_with_their_agent(pg_db):
    tenant_id, written = await _setup(pg_db)
    await pg_db.execute("DELETE FROM events WHERE event_id = $1", written[-1]["event_id"])
    await write_event(tenant_id=tenant_id, agent=AGENT, event="later", data={})
    assert await pg_db.fetchval("SELECT COUNT(*) FROM chain_head_breaks") == 1

    await pg_db.execute("DELETE FROM events WHERE tenant_id = $1", tenant_id)
    await pg_db.execute("DELETE FROM event_tombstones WHERE tenant_id = $1", tenant_id)
    await pg_db.execute("DELETE FROM agents WHERE tenant_id = $1", tenant_id)
    assert await pg_db.fetchval("SELECT COUNT(*) FROM chain_head_breaks") == 0
