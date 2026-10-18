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
EVENTS_PARENT_FK_DROP_SQL = """
    DO $$
    DECLARE
        fk record;
    BEGIN
        -- Every API worker runs this at startup. Only one attempts it; the
        -- others skip it instead of queueing behind it.
        IF NOT pg_try_advisory_xact_lock(hashtext('zizkadb:events_parent_fk_drop')) THEN
            RETURN;
        END IF;
        -- Dropping a constraint needs a brief exclusive lock on events, and
        -- queries on events queue behind that request. Give up after 2s on a
        -- busy table and retry on the next start.
        PERFORM set_config('lock_timeout', '2s', true);

        FOR fk IN
            SELECT c.conname
            FROM pg_constraint c
            JOIN pg_attribute a
              ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
            WHERE c.contype = 'f'
              AND c.conrelid = 'events'::regclass
              AND c.confrelid = 'events'::regclass
              AND a.attname = 'parent_event_id'
              AND array_length(c.conkey, 1) = 1  -- leave composite keys alone
        LOOP
            EXECUTE format('ALTER TABLE events DROP CONSTRAINT IF EXISTS %I', fk.conname);
        END LOOP;
    EXCEPTION
        WHEN lock_not_available THEN
            RAISE WARNING 'events.parent_event_id foreign key not dropped (events table busy); retrying on next start';
    END $$;
"""
