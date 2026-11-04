"""What context_for() and search should leave out, and how to rank what stays.

Superseded events (left out of memory, never deleted; the audit log keeps all):
- explicitly: a later event of the same agent has ``metadata.supersedes`` set
  to the event's id (``db.log(..., supersedes=old_id)``);
- implicitly: a ``STATE_SET`` all of whose keys were set again or deleted by a
  later ``STATE_SET`` / ``STATE_DELETE`` (the same rule time travel applies).

Ranking of relevant events: relevance × importance × recency, where
importance is ``metadata.importance`` in [0, 1] (unset counts as 0.5) and
recency halves every ``CONTEXT_RECENCY_HALF_LIFE_DAYS`` (default 30).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Iterable

DEFAULT_IMPORTANCE = 0.5


async def superseded_ids(pool, tenant_id: str, agent: str | None, event_ids: Iterable) -> set[str]:
    ids = list({str(e) for e in event_ids})
    if not ids:
        return set()
    rows = await pool.fetch(
        """
        SELECT e.event_id::text AS event_id
        FROM events e
        WHERE e.tenant_id = $1 AND ($2::text IS NULL OR e.agent_id = $2)
          AND e.event_id = ANY($3::uuid[])
          AND (
            EXISTS (
                SELECT 1 FROM events n
                WHERE n.tenant_id = $1
                  AND n.metadata->>'supersedes' = e.event_id::text
            )
            OR (
                e.event_type = 'STATE_SET'
                AND jsonb_typeof(e.data) = 'object'
                AND e.data <> '{}'::jsonb
                AND NOT EXISTS (
                    SELECT 1 FROM jsonb_object_keys(e.data) AS k(key)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM events l
                        WHERE l.tenant_id = $1 AND l.agent_id = e.agent_id
                          AND l.event_type IN ('STATE_SET', 'STATE_DELETE')
                          AND l.sequence_no > e.sequence_no
                          AND jsonb_typeof(l.data) = 'object'
                          AND ((l.event_type = 'STATE_SET' AND l.data ? k.key)
                               OR (l.event_type = 'STATE_DELETE' AND l.data->>'key' = k.key))
                    )
                )
            )
          )
        """,
        tenant_id, agent, ids,
    )
    return {r["event_id"] for r in rows}


def importance_of(metadata: Any) -> float:
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            return DEFAULT_IMPORTANCE
    if isinstance(metadata, dict):
        value = metadata.get("importance")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1:
            return float(value)
    return DEFAULT_IMPORTANCE


def half_life_days() -> float:
    try:
        return max(float(os.getenv("CONTEXT_RECENCY_HALF_LIFE_DAYS", "30")), 0.1)
    except ValueError:
        return 30.0


def weighted_score(relevance: float, timestamp: datetime, metadata: Any,
                   now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age_days = max((now - timestamp).total_seconds() / 86400, 0.0)
    recency = 0.5 ** (age_days / half_life_days())
    return relevance * (0.5 + importance_of(metadata)) * recency
