"""SQL that repairs constraints on the events table at startup."""

# events.parent_event_id originally had no ON DELETE action, so deleting an
# event that another event points to (GDPR forget, agent delete) failed with a
# foreign-key violation whenever the event had a causal child. Re-create the
# constraint as ON DELETE SET NULL: the child keeps its own record and loses
# only the link to the erased parent. Idempotent.
EVENTS_PARENT_FK_SET_NULL_SQL = """
    DO $$
    DECLARE
        fk_name text;
    BEGIN
        SELECT c.conname INTO fk_name
        FROM pg_constraint c
        JOIN pg_attribute a
          ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
        WHERE c.contype = 'f'
          AND c.conrelid = 'events'::regclass
          AND c.confrelid = 'events'::regclass
          AND a.attname = 'parent_event_id'
        LIMIT 1;

        IF fk_name IS NOT NULL AND (
            SELECT confdeltype FROM pg_constraint
            WHERE conname = fk_name AND conrelid = 'events'::regclass
        ) <> 'n' THEN
            EXECUTE format('ALTER TABLE events DROP CONSTRAINT %I', fk_name);
            fk_name := NULL;
        END IF;

        IF fk_name IS NULL THEN
            ALTER TABLE events
                ADD CONSTRAINT events_parent_event_id_fkey
                FOREIGN KEY (parent_event_id)
                REFERENCES events(event_id)
                ON DELETE SET NULL;
        END IF;
    END $$;
"""
