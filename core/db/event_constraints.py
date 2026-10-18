"""SQL that repairs constraints on the events table at startup."""

# events.parent_event_id used to reference events(event_id) with no ON DELETE
# action, so deleting an event that another event points to (GDPR forget,
# agent delete) failed with a foreign-key violation whenever it had a causal
# child.
#
# The link is not enforced by a foreign key. Erasing a parent leaves the
# child's parent_event_id as it was: the child row is never modified, so its
# event checksum (which covers parent_event_id) stays valid, and why() simply
# ends where the parent was erased. New links are still checked by the write
# path (services/event_write.py rejects a parent that does not exist or
# belongs to another tenant). Idempotent.
# Two-int advisory lock: a key space separate from the single-key hashtext()
# locks used per tenant and per event elsewhere, so they cannot collide.
_LOCK_CLASS, _LOCK_OBJ = 727001, 1

# Single-column foreign keys from events.parent_event_id to events. Composite
# keys that merely include the column are left alone.
_PARENT_FK_FILTER = """
    FROM pg_constraint c
    JOIN pg_attribute a
      ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
    WHERE c.contype = 'f'
      AND c.conrelid = 'events'::regclass
      AND c.confrelid = 'events'::regclass
      AND a.attname = 'parent_event_id'
      AND array_length(c.conkey, 1) = 1
"""

EVENTS_PARENT_FK_DROP_SQL = """
    DO $$
    DECLARE
        fk record;
    BEGIN
        -- Only one worker attempts the drop; the others skip it here and wait
        -- for it in remaining_parent_fks().
        IF NOT pg_try_advisory_xact_lock(""" + f"{_LOCK_CLASS}, {_LOCK_OBJ}" + """) THEN
            RETURN;
        END IF;
        -- Dropping a constraint needs a brief exclusive lock on events, and
        -- queries on events queue behind that request. Give up after 2s on a
        -- busy table and retry on the next start.
        PERFORM set_config('lock_timeout', '2s', true);

        FOR fk IN SELECT c.conname """ + _PARENT_FK_FILTER + """
        LOOP
            EXECUTE format('ALTER TABLE events DROP CONSTRAINT IF EXISTS %I', fk.conname);
        END LOOP;
    EXCEPTION
        WHEN lock_not_available THEN
            RAISE WARNING 'events.parent_event_id foreign key not dropped (events table busy); retrying on next start';
    END $$;
"""

EVENTS_PARENT_FK_COUNT_SQL = "SELECT count(*) " + _PARENT_FK_FILTER


async def remaining_parent_fks(pool) -> int:
    """Parent FKs still present, after any worker's in-progress drop finishes.

    A worker that skipped the drop because another one holds the lock waits
    for that worker's transaction here, so it does not report an FK that is
    about to be dropped.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1, $2)", _LOCK_CLASS, _LOCK_OBJ)
            return await conn.fetchval(EVENTS_PARENT_FK_COUNT_SQL)


async def drop_parent_fks(pool) -> int:
    """Drop any parent FK on events; return how many remain (0 when done).

    Called on every startup. Once the FK is gone this is one catalog query.
    """
    if not await pool.fetchval(EVENTS_PARENT_FK_COUNT_SQL):
        return 0
    await pool.execute(EVENTS_PARENT_FK_DROP_SQL)
    return await remaining_parent_fks(pool)
