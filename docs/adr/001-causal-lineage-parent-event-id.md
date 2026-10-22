# ADR-001: Causal Lineage via `parent_event_id` on the `events` Table

**Status**: Accepted  
**Date**: 2024

---

## Context

ZizkaDB's core value proposition is answering "why did my agent do that?" — tracing the chain of decisions that led to any agent action. We needed a mechanism to represent causal relationships between events.

Options considered:

1. A separate `causality` or `event_links` table (`parent_id`, `child_id`, `relationship_type`)
2. A `parent_event_id` foreign key directly on the `events` table
3. A `causes` JSONB array on each event containing event IDs

The primary query pattern is: given event E, walk backward through the chain of events that caused it — used by `db.why()`.

---

## Decision

Store causal relationships as a `parent_event_id UUID` column directly on the `events` table. (Originally a self-referential foreign key; see the amendment below: it is no longer a foreign key, and must not become one again.)

The `db.why()` query uses a PostgreSQL **recursive CTE** starting from any event and following `parent_event_id` links backward:

```sql
WITH RECURSIVE causal_chain AS (
    SELECT * FROM events WHERE event_id = $1
    UNION ALL
    SELECT e.* FROM events e
    JOIN causal_chain c ON e.event_id = c.parent_event_id
)
SELECT * FROM causal_chain ORDER BY timestamp ASC;
```

---

## Consequences

**Better:**
- Zero joins — the full causal chain is one recursive CTE on a single table
- `parent_event_id` is indexed (`idx_events_parent`) for efficient lookup
- Causality is captured at write time — SDKs pass `parent_id=` to `db.log()`
- Schema is simple — one nullable column, no extra tables

**Worse:**
- Only supports tree-shaped causality (one parent per event) — not a DAG
- Multi-parent causality (event caused by two prior events) cannot be represented without a separate table
- Circular references would cause infinite recursion in the CTE (guarded by PostgreSQL's cycle detection)

**Accepted limitations:**
The vast majority of agent workflows produce linear or tree-shaped causal chains. Multi-parent causality (e.g., a decision that depends on two independent prior queries) is an edge case that can be handled at the application level by choosing the most direct parent. If DAG causality becomes a product requirement, an `event_parents` junction table can be added without removing `parent_event_id`.

---

## Alternatives considered

**Separate `causality` table**: adds a join on every `db.why()` call; more flexible for DAGs but unnecessary complexity for the common case.

**`causes` JSONB array**: makes the recursive CTE much harder to write and harder to index.


## Amendment (October 2026): no foreign key

`parent_event_id` is no longer a foreign key (`core/db/event_constraints.py` drops it on startup; `core/db/schema.sql` no longer creates it).

- **Why:** with the foreign key, GDPR forget and agent delete failed whenever a deleted event had a causal child. `ON DELETE SET NULL` would have fixed that by rewriting the children, but the v2 event checksum covers `parent_event_id`, so every child of a forgotten event would then fail verification.
- **Now:** erasing a parent leaves its children exactly as they were. Their checksums still verify, and `why()` ends where the parent was erased. New links are still checked when an event is written (`services/event_write.py`: the parent must exist in the same tenant).
- **Do not re-add the foreign key**, with or without `ON DELETE SET NULL`.
