"""Per-agent report aggregation.

Composes a single, date-ranged report for one agent from the events table. Every
number is derived from real events — nothing is estimated or fabricated — so a
report can be handed to a stakeholder as-is.

This module is also the canonical home for the small analytics primitives
(`distribution_pct`, `l1_distance`, `biggest_changes`, `window_metrics`, the
drift score/verdict) that the baseline and behavior-change endpoints in
``api/agents.py`` reuse. Keeping them here (a service, imported by the api layer)
avoids duplicating the logic and avoids a circular import — the report route in
``api/agents.py`` imports this module, and this module imports nothing from the
api layer.

All time bucketing is done in UTC. The error heuristic mirrors the rest of the
codebase: an event counts as an error when its ``event_type`` matches
``%error%``/``%fail%`` or its ``data`` payload has an ``error`` key.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from services.actions import action_breakdown

REPORT_VERSION = "1.0"

_VALID_GRANULARITY = {"day", "week"}


def _iso_utc(dt: datetime) -> str:
    """ISO-8601 with an explicit UTC offset, so clients never parse it as local."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()

# A behavior baseline needs at least this many events before the window to be
# statistically meaningful (mirrors api/agents.py::MINIMUM_BASELINE_EVENTS).
MINIMUM_BASELINE_EVENTS = 50

# Longest span a single report may cover, to bound query cost.
MAX_RANGE_DAYS = 366

# Error classification shared by every aggregate here.
_ERROR_PREDICATE = (
    "event_type ILIKE '%error%' OR event_type ILIKE '%fail%' OR data ? 'error'"
)


# ── Pure analytics primitives (canonical home) ─────────────────────────────


def distribution_pct(rows, key: str) -> dict[str, float]:
    """Convert ``[{key: ..., 'count': N}]`` rows into a percentage distribution."""
    total = sum(r["count"] for r in rows) or 1
    return {r[key]: round(100 * r["count"] / total, 2) for r in rows}


def l1_distance(a: dict[str, float], b: dict[str, float]) -> float:
    """L1 distance between two percentage distributions, normalised to [0, 1]."""
    keys = set(a) | set(b)
    diff = sum(abs(a.get(k, 0) - b.get(k, 0)) for k in keys)
    return round(diff / 200, 4)  # max possible is 200pp -> 1.0


def biggest_changes(
    label: str,
    baseline: dict[str, float],
    recent: dict[str, float],
    top: int = 5,
) -> list[dict]:
    """Largest per-key deltas between two distributions, most-changed first."""
    keys = set(baseline) | set(recent)
    deltas = []
    for k in keys:
        b = baseline.get(k, 0)
        r = recent.get(k, 0)
        deltas.append(
            {"metric": f"{label}.{k}", "baseline": b, "recent": r, "delta_pp": round(r - b, 2)}
        )
    deltas.sort(key=lambda d: abs(d["delta_pp"]), reverse=True)
    return [d for d in deltas[:top] if d["delta_pp"] != 0]


def drift_verdict(behavior_change_pct: float) -> str:
    """Map a 0-100 behavior-change score to a verdict the dashboard understands."""
    if behavior_change_pct < 5:
        return "stable"
    if behavior_change_pct < 15:
        return "minor_drift"
    if behavior_change_pct < 30:
        return "noticeable_drift"
    return "significant_drift"


# ── Time / validation helpers ──────────────────────────────────────────────


def parse_utc_naive(value: str, field: str) -> datetime:
    """Parse an ISO-8601 string to a naive-UTC datetime.

    The events table stores timestamps compared against naive-UTC values
    elsewhere in the codebase, so an offset-aware input (``...+05:00``) is first
    converted to UTC and then made naive — never merely stripped, which would
    misplace the instant.
    """
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def resolve_granularity(from_dt: datetime, to_dt: datetime, requested: str | None) -> str:
    """Pick a bucket size that keeps the series readable and bounded.

    ``day`` for spans up to 92 days, otherwise ``week``. An explicit request is
    honoured only when it wouldn't blow up the bucket count.
    """
    span_days = (to_dt - from_dt).days
    auto = "day" if span_days <= 92 else "week"
    if requested in ("day", "week"):
        # Never allow 'day' on a very long custom range.
        if requested == "day" and span_days > 92:
            return "week"
        return requested
    return auto


def validate_range(from_dt: datetime, to_dt: datetime) -> None:
    """Raise ``ValueError`` for an incoherent or oversized range."""
    if from_dt >= to_dt:
        raise ValueError("'from' must be earlier than 'to'")
    if (to_dt - from_dt).days > MAX_RANGE_DAYS:
        raise ValueError(f"Range is limited to {MAX_RANGE_DAYS} days")


# ── Window aggregation ─────────────────────────────────────────────────────


async def window_metrics(pool, tenant_id: str, agent_id: str, from_ts, to_ts) -> dict:
    """Event distribution, transitions and error rate for ``[from_ts, to_ts)``.

    ``from_ts=None`` means "everything before ``to_ts``" (the baseline epoch).
    Canonical implementation reused by the baseline/behavior-change endpoints.
    """
    if from_ts is None:
        ts_filter = "timestamp < $3"
        params = (tenant_id, agent_id, to_ts)
    else:
        ts_filter = "timestamp >= $3 AND timestamp < $4"
        params = (tenant_id, agent_id, from_ts, to_ts)

    type_rows = await pool.fetch(
        f"""
        SELECT event_type AS key, COUNT(*) AS count
        FROM events
        WHERE tenant_id = $1 AND agent_id = $2 AND {ts_filter}
        GROUP BY event_type
        """,
        *params,
    )

    transition_rows = await pool.fetch(
        f"""
        SELECT p.event_type || ' -> ' || c.event_type AS key, COUNT(*) AS count
        FROM events c
        JOIN events p ON c.parent_event_id = p.event_id AND p.tenant_id = c.tenant_id
        WHERE c.tenant_id = $1 AND c.agent_id = $2
          AND {ts_filter.replace('timestamp', 'c.timestamp')}
        GROUP BY p.event_type, c.event_type
        ORDER BY count DESC
        LIMIT 20
        """,
        *params,
    )

    error_row = await pool.fetchrow(
        f"""
        SELECT COUNT(*) FILTER (WHERE {_ERROR_PREDICATE})::float * 100.0
               / NULLIF(COUNT(*), 0) AS pct
        FROM events
        WHERE tenant_id = $1 AND agent_id = $2 AND {ts_filter}
        """,
        *params,
    )

    return {
        "events": sum(r["count"] for r in type_rows),
        "event_distribution": distribution_pct(type_rows, "key"),
        "transitions": distribution_pct(transition_rows, "key"),
        "error_rate_pct": round(error_row["pct"] or 0, 2),
    }


async def _summary(pool, tenant_id, agent_id, from_dt, to_dt) -> dict:
    row = await pool.fetchrow(
        f"""
        SELECT
            COUNT(*)                                   AS total_events,
            COUNT(DISTINCT event_type)                 AS unique_event_types,
            COUNT(DISTINCT session_id)                 AS sessions,
            COUNT(DISTINCT date_trunc('day', timestamp)) AS active_days,
            MIN(timestamp)                             AS first_event,
            MAX(timestamp)                             AS last_event,
            COUNT(*) FILTER (WHERE {_ERROR_PREDICATE}) AS error_count
        FROM events
        WHERE tenant_id = $1 AND agent_id = $2
          AND timestamp >= $3 AND timestamp < $4
        """,
        tenant_id, agent_id, from_dt, to_dt,
    )
    total = row["total_events"] or 0
    errors = row["error_count"] or 0
    return {
        "total_events": total,
        "unique_event_types": row["unique_event_types"] or 0,
        "sessions": row["sessions"] or 0,
        "active_days": row["active_days"] or 0,
        "error_count": errors,
        "error_rate_pct": round(errors * 100.0 / total, 2) if total else 0.0,
        "first_event": row["first_event"].isoformat() if row["first_event"] else None,
        "last_event": row["last_event"].isoformat() if row["last_event"] else None,
    }


async def _timeseries(pool, tenant_id, agent_id, from_dt, to_dt, granularity) -> list[dict]:
    """Gap-filled per-bucket counts so zero-activity buckets appear as 0.

    ``granularity`` is a whitelisted literal ('day'/'week'), safe to interpolate.
    """
    if granularity not in _VALID_GRANULARITY:  # defence-in-depth vs. SQL interpolation
        raise ValueError(f"invalid granularity: {granularity!r}")
    step = f"1 {granularity}"
    rows = await pool.fetch(
        f"""
        WITH buckets AS (
            SELECT generate_series(
                date_trunc('{granularity}', $3::timestamp),
                date_trunc('{granularity}', $4::timestamp - interval '1 microsecond'),
                interval '{step}'
            ) AS bucket
        ),
        ev AS (
            SELECT
                date_trunc('{granularity}', timestamp) AS bucket,
                COUNT(*)                                AS events,
                COUNT(*) FILTER (WHERE {_ERROR_PREDICATE}) AS errors,
                COUNT(DISTINCT session_id)              AS sessions
            FROM events
            WHERE tenant_id = $1 AND agent_id = $2
              AND timestamp >= $3 AND timestamp < $4
            GROUP BY 1
        )
        SELECT b.bucket,
               COALESCE(ev.events, 0)   AS events,
               COALESCE(ev.errors, 0)   AS errors,
               COALESCE(ev.sessions, 0) AS sessions
        FROM buckets b
        LEFT JOIN ev ON ev.bucket = b.bucket
        ORDER BY b.bucket
        """,
        tenant_id, agent_id, from_dt, to_dt,
    )
    return [
        {
            "bucket": _iso_utc(r["bucket"]),
            "events": r["events"],
            "errors": r["errors"],
            "sessions": r["sessions"],
        }
        for r in rows
    ]


async def _top_events(pool, tenant_id, agent_id, from_dt, to_dt) -> list[dict]:
    """Top-10 event types (raw counts). ``pct`` is filled in by the caller once
    the window total is known, so this can run in parallel with the summary."""
    rows = await pool.fetch(
        """
        SELECT event_type, COUNT(*) AS count
        FROM events
        WHERE tenant_id = $1 AND agent_id = $2
          AND timestamp >= $3 AND timestamp < $4
        GROUP BY event_type
        ORDER BY count DESC
        LIMIT 10
        """,
        tenant_id, agent_id, from_dt, to_dt,
    )
    return [{"event_type": r["event_type"], "count": r["count"]} for r in rows]


async def _sessions(pool, tenant_id, agent_id, from_dt, to_dt) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT
            session_id,
            COUNT(*)                    AS event_count,
            COUNT(DISTINCT event_type)  AS event_types,
            MIN(timestamp)              AS started_at,
            MAX(timestamp)              AS ended_at,
            EXTRACT(EPOCH FROM (MAX(timestamp) - MIN(timestamp)))::int AS duration_seconds
        FROM events
        WHERE tenant_id = $1 AND agent_id = $2
          AND session_id IS NOT NULL
          AND timestamp >= $3 AND timestamp < $4
        GROUP BY session_id
        ORDER BY MAX(timestamp) DESC
        LIMIT 100
        """,
        tenant_id, agent_id, from_dt, to_dt,
    )
    return [
        {
            "session_id": r["session_id"],
            "event_count": r["event_count"],
            "event_types": r["event_types"],
            "started_at": r["started_at"].isoformat(),
            "ended_at": r["ended_at"].isoformat(),
            "duration_seconds": r["duration_seconds"] or 0,
        }
        for r in rows
    ]


async def _drift(pool, tenant_id, agent_id, from_dt, to_dt) -> dict | None:
    """Behavior drift of the window vs. everything before it.

    Returns ``None`` when there isn't enough history before the window to form a
    meaningful baseline — an honest "not enough data" rather than a fake score.
    """
    baseline_count = (
        await pool.fetchval(
            "SELECT COUNT(*) FROM events WHERE tenant_id = $1 AND agent_id = $2 AND timestamp < $3",
            tenant_id, agent_id, from_dt,
        )
        or 0
    )
    if baseline_count < MINIMUM_BASELINE_EVENTS:
        return None

    baseline = await window_metrics(pool, tenant_id, agent_id, None, from_dt)
    recent = await window_metrics(pool, tenant_id, agent_id, from_dt, to_dt)

    dist_shift = l1_distance(baseline["event_distribution"], recent["event_distribution"])
    trans_shift = l1_distance(baseline["transitions"], recent["transitions"])
    error_delta = recent["error_rate_pct"] - baseline["error_rate_pct"]
    error_component = min(abs(error_delta) / 20.0, 1.0)

    score = 0.4 * dist_shift + 0.4 * trans_shift + 0.2 * error_component
    change_pct = round(score * 100, 1)

    biggest = biggest_changes(
        "event_distribution", baseline["event_distribution"], recent["event_distribution"]
    ) + biggest_changes("transitions", baseline["transitions"], recent["transitions"])
    biggest.sort(key=lambda d: abs(d["delta_pp"]), reverse=True)

    return {
        "score": round(score, 4),
        "verdict": drift_verdict(change_pct),
        "behavior_change_pct": change_pct,
        "error_rate_change_pp": round(error_delta, 2),
        "biggest_changes": biggest[:8],
    }


# ── Health + recommendations (deterministic, cite the metric) ──────────────


def derive_health(summary: dict, drift: dict | None) -> str:
    """Single health verdict for the window (tones match the dashboard)."""
    if summary["total_events"] == 0:
        return "idle"
    verdict = drift["verdict"] if drift else None
    if summary["error_rate_pct"] > 10:
        return "error"
    if verdict in ("noticeable_drift", "significant_drift"):
        return "drift"
    if summary["error_rate_pct"] > 5 or verdict == "minor_drift":
        return "attention"
    return "healthy"


def build_recommendations(summary: dict, drift: dict | None, previous: dict) -> list[dict]:
    """Rule-based, deterministic recommendations. Each cites the metric behind it."""
    recs: list[dict] = []

    if summary["total_events"] == 0:
        recs.append(
            {
                "severity": "info",
                "category": "observability",
                "title": "No activity recorded in this period",
                "detail": "This agent logged no events in the selected range. Start logging events "
                "with the SDK to build a report.",
            }
        )
        return recs

    if summary["error_rate_pct"] > 10:
        recs.append(
            {
                "severity": "critical",
                "category": "reliability",
                "title": f"High error rate ({summary['error_rate_pct']}%)",
                "detail": f"{summary['error_count']} of {summary['total_events']} events are errors. "
                "Investigate the failing operations and add retries or guards.",
            }
        )
    elif summary["error_rate_pct"] > 5:
        recs.append(
            {
                "severity": "warning",
                "category": "reliability",
                "title": f"Elevated error rate ({summary['error_rate_pct']}%)",
                "detail": "Error rate is above a healthy threshold. Review the most common error "
                "events in the breakdown below.",
            }
        )

    prev_err = previous.get("error_rate_pct", 0)
    if prev_err and summary["error_rate_pct"] > prev_err + 2:
        recs.append(
            {
                "severity": "warning",
                "category": "reliability",
                "title": "Error rate is rising",
                "detail": f"Error rate rose from {prev_err}% to {summary['error_rate_pct']}% vs the "
                "previous period. Worth investigating before it grows.",
            }
        )

    if drift and drift["verdict"] in ("noticeable_drift", "significant_drift"):
        recs.append(
            {
                "severity": "warning",
                "category": "behavior",
                "title": f"Behavior drift detected ({drift['behavior_change_pct']}%)",
                "detail": "This agent is behaving differently from its historical baseline. Check the "
                "biggest changes to confirm it's intended and not a regression.",
            }
        )

    if summary["total_events"] > 0 and summary["sessions"] == 0:
        recs.append(
            {
                "severity": "info",
                "category": "observability",
                "title": "Events aren't grouped into sessions",
                "detail": "Pass a session_id when logging events to unlock session analytics and "
                "clearer traces.",
            }
        )

    if summary["unique_event_types"] == 1:
        recs.append(
            {
                "severity": "info",
                "category": "observability",
                "title": "Only one event type is logged",
                "detail": "Richer instrumentation (tool calls, decisions, errors as distinct event "
                "types) makes reports far more useful.",
            }
        )

    if not recs:
        recs.append(
            {
                "severity": "positive",
                "category": "health",
                "title": "No issues found",
                "detail": "This agent is healthy for the selected period: error rate is low and "
                "behavior is consistent with its baseline.",
            }
        )
    return recs


# ── Composition ────────────────────────────────────────────────────────────


async def build_report(
    pool,
    tenant_id: str,
    agent_id: str,
    from_dt: datetime,
    to_dt: datetime,
    granularity: str,
) -> dict:
    """Assemble the full report payload for ``[from_dt, to_dt)``."""
    span = to_dt - from_dt
    prev_from = from_dt - span

    # `summary` is needed for the top-event percentages, health and
    # recommendations, so resolve it first; the rest are independent and run
    # concurrently (≈2 round-trips total rather than ~8 sequential ones).
    summary = await _summary(pool, tenant_id, agent_id, from_dt, to_dt)

    prev_summary, timeseries, top_raw, distribution, sessions, drift, actions = await asyncio.gather(
        _summary(pool, tenant_id, agent_id, prev_from, from_dt),
        _timeseries(pool, tenant_id, agent_id, from_dt, to_dt, granularity),
        _top_events(pool, tenant_id, agent_id, from_dt, to_dt),
        window_metrics(pool, tenant_id, agent_id, from_dt, to_dt),
        _sessions(pool, tenant_id, agent_id, from_dt, to_dt),
        _drift(pool, tenant_id, agent_id, from_dt, to_dt),
        action_breakdown(pool, tenant_id, agent_id, from_ts=from_dt, to_ts=to_dt),
    )

    total = summary["total_events"]
    top_events = [
        {**r, "pct": round(100 * r["count"] / total, 2) if total else 0.0} for r in top_raw
    ]

    summary["previous"] = {
        "total_events": prev_summary["total_events"],
        "sessions": prev_summary["sessions"],
        "error_rate_pct": prev_summary["error_rate_pct"],
    }
    summary["health"] = derive_health(summary, drift)

    return {
        "agent": agent_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "report_version": REPORT_VERSION,
        "period": {
            "from": _iso_utc(from_dt),
            "to": _iso_utc(to_dt),
            "days": span.days,
            "granularity": granularity,
        },
        "summary": summary,
        "timeseries": timeseries,
        "top_events": top_events,
        "distribution": {
            "event_distribution": distribution["event_distribution"],
            "transitions": distribution["transitions"],
        },
        # Which tools / APIs were called in the period, with error rates.
        "actions": actions,
        "sessions": sessions,
        "drift": drift,
        "recommendations": build_recommendations(summary, drift, summary["previous"]),
    }
