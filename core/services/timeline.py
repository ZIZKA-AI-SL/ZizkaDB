"""
Event timeline for one agent over a time window: what the dashboard's
timeline graph draws.

One query pass returns:

- ``buckets``: event counts per time bucket, per event type, with an error
  count. The bucket size follows the window (1 minute for an hour, 30 minutes
  for a day, a few hours for a week), so the graph stays readable at any zoom.
- ``events``: every event in the window, as light rows (no payload), when there
  are at most ``POINT_LIMIT`` of them; ``None`` otherwise (the graph then
  groups ordinary events per bucket).
- ``errors``: the window's error events as individual rows, always, up to
  ``ERROR_LIMIT``, so no failure is hidden by grouping.

Errors use the same rule as reports (``services/reports.py``): the event type
contains "error" or "fail", or the event's data has an ``error`` key.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from services.reports import _ERROR_PREDICATE

# Bucket sizes in seconds, smallest first; the first that gives at most
# MAX_BUCKETS buckets is used.
BUCKET_LADDER = (
    60, 120, 300, 600, 900, 1800,
    3600, 3 * 3600, 6 * 3600, 12 * 3600,
    86400, 7 * 86400,
)
MAX_BUCKETS = 120
POINT_LIMIT = 1500
ERROR_LIMIT = 1000


def bucket_seconds(from_dt: datetime, to_dt: datetime) -> int:
    span = (to_dt - from_dt).total_seconds()
    for size in BUCKET_LADDER:
        # +1: a window of exactly N slots plus a few seconds still fits.
        if span / size <= MAX_BUCKETS + 1:
            return size
    return BUCKET_LADDER[-1]


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _light(row: Any) -> dict:
    return {
        "event_id": str(row["event_id"]),
        "timestamp": _iso(row["timestamp"]),
        "event": row["event_type"],
        "session_id": row["session_id"],
        "parent_id": str(row["parent_event_id"]) if row["parent_event_id"] else None,
        "is_error": bool(row["is_error"]),
    }


_LIGHT_COLUMNS = f"""
    event_id, timestamp, event_type, session_id, parent_event_id,
    ({_ERROR_PREDICATE}) AS is_error
"""


async def build_timeline(
    pool, tenant_id: str, agent_id: str, from_dt: datetime, to_dt: datetime
) -> dict:
    size = bucket_seconds(from_dt, to_dt)
    window = "tenant_id = $1 AND agent_id = $2 AND timestamp >= $3 AND timestamp < $4"
    args = (tenant_id, agent_id, from_dt, to_dt)

    bucket_rows = await pool.fetch(
        f"""
        SELECT to_timestamp(floor(extract(epoch FROM timestamp) / $5) * $5) AS bucket,
               event_type,
               COUNT(*) AS n,
               COUNT(*) FILTER (WHERE {_ERROR_PREDICATE}) AS errors
        FROM events
        WHERE {window}
        GROUP BY 1, 2
        ORDER BY 1, 2
        """,
        *args,
        size,
    )

    buckets: dict[datetime, dict] = {}
    types: dict[str, dict] = {}
    total = errors_total = 0
    for r in bucket_rows:
        b = buckets.setdefault(r["bucket"], {"start": _iso(r["bucket"]), "total": 0, "errors": 0, "by_type": {}})
        n, e = int(r["n"]), int(r["errors"])
        b["total"] += n
        b["errors"] += e
        b["by_type"][r["event_type"]] = n
        t = types.setdefault(r["event_type"], {"event": r["event_type"], "count": 0, "errors": 0})
        t["count"] += n
        t["errors"] += e
        total += n
        errors_total += e

    events = None
    if total <= POINT_LIMIT:
        rows = await pool.fetch(
            f"SELECT {_LIGHT_COLUMNS} FROM events WHERE {window} "
            "ORDER BY timestamp, sequence_no",
            *args,
        )
        events = [_light(r) for r in rows]

    error_rows = await pool.fetch(
        f"SELECT {_LIGHT_COLUMNS} FROM events WHERE {window} AND ({_ERROR_PREDICATE}) "
        "ORDER BY timestamp DESC, sequence_no DESC LIMIT $5",
        *args,
        ERROR_LIMIT,
    )
    errors = [_light(r) for r in reversed(error_rows)]

    return {
        "agent": agent_id,
        "from": _iso(from_dt),
        "to": _iso(to_dt),
        "bucket_seconds": size,
        "total": total,
        "error_total": errors_total,
        "types": sorted(types.values(), key=lambda t: (-t["count"], t["event"])),
        "buckets": list(buckets.values()),
        "events": events,
        "errors": errors,
        "errors_truncated": errors_total > len(errors),
        "point_limit": POINT_LIMIT,
    }
