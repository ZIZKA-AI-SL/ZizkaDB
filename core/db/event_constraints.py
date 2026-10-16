"""SQL that repairs constraints on the events table at startup."""

# events.parent_event_id originally had no ON DELETE action, so deleting an
# event that another event points to (GDPR forget, agent delete) failed with a
# foreign-key violation whenever the event had a causal child. Re-create the
# constraint as ON DELETE SET NULL: the child keeps its own record and loses
# only the link to the erased parent. Idempotent.
EVENTS_PARENT_FK_SET_NULL_SQL = """
    DO $$
    DECLARE
        fk record;
        has_set_null boolean := false;
    BEGIN
        -- Every API worker runs this at startup: take turns, so the others
        -- find the constraint already converted and change nothing.
        PERFORM pg_advisory_xact_lock(hashtext('zizkadb:events_parent_fk_set_null'));
        -- Dropping and adding a constraint needs a brief exclusive lock on
        -- events. Do not let a long transaction stall startup behind it:
        -- give up after 5s and retry on the next start.
        PERFORM set_config('lock_timeout', '5s', true);

        FOR fk IN
            SELECT c.conname, c.confdeltype
            FROM pg_constraint c
            JOIN pg_attribute a
              ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
            WHERE c.contype = 'f'
              AND c.conrelid = 'events'::regclass
              AND c.confrelid = 'events'::regclass
              AND a.attname = 'parent_event_id'
        LOOP
            IF fk.confdeltype = 'n' THEN
                has_set_null := true;
            ELSE
                EXECUTE format('ALTER TABLE events DROP CONSTRAINT IF EXISTS %I', fk.conname);
            END IF;
        END LOOP;

        -- NOT VALID skips re-checking existing rows, which would scan the
        -- whole table under that lock. On an upgrade the constraint being
        -- replaced already guaranteed those rows; new and updated rows are
        -- always checked.
        IF NOT has_set_null THEN
            ALTER TABLE events
                ADD CONSTRAINT events_parent_event_id_fkey
                FOREIGN KEY (parent_event_id)
                REFERENCES events(event_id)
                ON DELETE SET NULL
                NOT VALID;
        END IF;
    EXCEPTION
        WHEN lock_not_available THEN
            RAISE WARNING 'events.parent_event_id foreign key not converted to ON DELETE SET NULL (events table busy); retrying on next start';
    END $$;
"""
