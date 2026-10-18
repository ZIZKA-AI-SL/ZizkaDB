"""Deterministic evidence extraction — the accuracy core.

Reads *real facts* from the events table and turns them into typed ``Evidence``
signals, each only when it crosses a configured threshold. Claude is later given
ONLY these signals, so it cannot introduce a problem the data doesn't show.

Design: the DB layer (``extract_evidence``) fetches raw aggregates; the pure
``_detect_*`` builders turn those aggregates into ``Evidence`` — so every
detector is unit-testable without a database.

Reuses ``services.reports`` (``_summary``, ``window_metrics``, ``_sessions``,
``_drift``, the shared error predicate) rather than duplicating aggregation.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from services import reports
from services.ai import config
from services.suggestions.models import Evidence, EvidenceBundle

_THRESH = config.THRESHOLDS
_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9]{6,}|Bearer\s+\S+|[A-Fa-f0-9]{32,}|eyJ[A-Za-z0-9_\-]{10,})")


# ── helpers ────────────────────────────────────────────────────────────────


def _scrub(text: str | None) -> str:
    """Cap length and redact anything that looks like a secret before it leaves
    the server for the model."""
    if not text:
        return ""
    text = _SECRET_RE.sub("[redacted]", str(text))
    return text[: config.MAX_SAMPLE_CHARS]


def _scale(value: float, low: float, high: float) -> int:
    """Map ``value`` in [low, high] to a strength in [50, 90] (clamped).

    Barely over threshold → ~50; strongly over → ~90. Confidence of any
    suggestion citing the signal is later clamped to this.
    """
    if high <= low:
        return 70
    ratio = (value - low) / (high - low)
    return max(50, min(90, round(50 + 40 * ratio)))


# ── pure detectors (no DB — unit-testable) ─────────────────────────────────


def _detect_low_success_rate(summary: dict[str, Any]) -> Evidence | None:
    rate = summary.get("error_rate_pct", 0) or 0
    total = summary.get("total_events", 0) or 0
    errors = summary.get("error_count", 0) or 0
    if rate < _THRESH["error_rate_pct"]:
        return None
    return Evidence(
        id="low_success_rate",
        category="reliability",
        label="Low success rate",
        summary=f"{errors} of {total} events ({rate}%) are errors/failures.",
        metrics={"error_rate_pct": rate, "error_count": errors, "total_events": total},
        strength=_scale(rate, _THRESH["error_rate_pct"], 40),
    )


def _detect_recurring_errors(rows: list[dict[str, Any]]) -> Evidence | None:
    recurring = [r for r in rows if r["count"] >= _THRESH["recurring_error_min_count"]]
    if not recurring:
        return None
    top = recurring[0]
    samples = [f"{_scrub(r['error_key'])} ×{r['count']}" for r in recurring[:config.MAX_SAMPLES_PER_SIGNAL]]
    return Evidence(
        id="recurring_errors",
        category="error_prevention",
        label="Recurring errors",
        summary=f"A specific error recurred {top['count']} times ({len(recurring)} distinct recurring errors).",
        metrics={"distinct": len(recurring), "top_count": top["count"]},
        samples=samples,
        strength=_scale(top["count"], _THRESH["recurring_error_min_count"], 30),
    )


def _detect_retry_loops(rows: list[dict[str, Any]]) -> Evidence | None:
    loops = [r for r in rows if r["repeat_count"] >= _THRESH["retry_loop_min_repeats"]]
    if not loops:
        return None
    top = loops[0]
    samples = [f"{_scrub(r['event_type'])} repeated ×{r['repeat_count']}" for r in loops[:config.MAX_SAMPLES_PER_SIGNAL]]
    return Evidence(
        id="retry_loops",
        category="performance",
        label="Retry loops / duplicate calls",
        summary=f"'{_scrub(top['event_type'])}' repeats back-to-back within sessions "
        f"({top['repeat_count']} consecutive-repeat occurrences) — likely retries or duplicate tool calls.",
        metrics={"top_event": top["event_type"], "repeat_count": top["repeat_count"]},
        samples=samples,
        strength=_scale(top["repeat_count"], _THRESH["retry_loop_min_repeats"], 25),
    )


def _detect_long_chains(max_depth: int, avg_depth: float) -> Evidence | None:
    if max_depth < _THRESH["long_chain_depth"]:
        return None
    return Evidence(
        id="long_causal_chains",
        category="general",
        label="Long reasoning chains",
        summary=f"The deepest causal chain is {max_depth} steps (avg {avg_depth:.1f}); "
        "long chains can indicate over-reasoning or missing task decomposition.",
        metrics={"max_depth": max_depth, "avg_depth": round(avg_depth, 1)},
        strength=_scale(max_depth, _THRESH["long_chain_depth"], 40),
    )


def _detect_missing_sessions(null_count: int, total: int) -> Evidence | None:
    if total == 0:
        return None
    pct = round(100 * null_count / total, 1)
    if pct < _THRESH["missing_session_pct"]:
        return None
    return Evidence(
        id="missing_sessions",
        category="general",
        label="Events not grouped into sessions",
        summary=f"{pct}% of events have no session_id, limiting traceability and session analytics.",
        metrics={"missing_pct": pct, "missing_count": null_count, "total_events": total},
        strength=_scale(pct, _THRESH["missing_session_pct"], 90),
    )


def _detect_low_diversity(summary: dict[str, Any]) -> Evidence | None:
    types = summary.get("unique_event_types", 0) or 0
    total = summary.get("total_events", 0) or 0
    if types != 1 or total < _THRESH["min_events"]:
        return None
    return Evidence(
        id="low_event_diversity",
        category="general",
        label="Only one event type logged",
        summary="Every event is the same type — richer instrumentation (tool calls, decisions, "
        "errors as distinct types) would make analysis far more useful.",
        metrics={"unique_event_types": types, "total_events": total},
        strength=60,
    )


def _detect_slow_sessions(slow: list[dict[str, Any]], total_sessions: int) -> Evidence | None:
    if not slow:
        return None
    slowest = max(s["duration_seconds"] for s in slow)
    return Evidence(
        id="slow_sessions",
        category="performance",
        label="Slow sessions",
        summary=f"{len(slow)} of {total_sessions} sessions run longer than "
        f"{_THRESH['slow_session_seconds']}s (slowest {slowest}s).",
        metrics={"slow_count": len(slow), "total_sessions": total_sessions, "slowest_seconds": slowest},
        strength=_scale(len(slow), 1, max(2, total_sessions)),
    )


def _detect_drift(drift: dict[str, Any] | None) -> Evidence | None:
    if not drift or drift.get("verdict") not in ("noticeable_drift", "significant_drift"):
        return None
    pct = drift.get("behavior_change_pct", 0)
    return Evidence(
        id="behavior_drift",
        category="reliability",
        label="Behavior drift",
        summary=f"Recent behavior diverges {pct}% from this agent's historical baseline "
        f"({drift.get('verdict').replace('_', ' ')}).",
        metrics={"behavior_change_pct": pct, "verdict": drift.get("verdict")},
        strength=_scale(pct, 15, 50),
    )


def _detect_sparse_activity(summary: dict[str, Any]) -> Evidence | None:
    active_days = summary.get("active_days", 0) or 0
    total = summary.get("total_events", 0) or 0
    if active_days > _THRESH["sparse_active_days"] or total < _THRESH["min_events"]:
        return None
    return Evidence(
        id="sparse_activity",
        category="general",
        label="Sparse observability",
        summary=f"Activity is concentrated in {active_days} day(s) — more consistent logging would "
        "improve monitoring and drift detection.",
        metrics={"active_days": active_days, "total_events": total},
        strength=55,
    )


def _detect_token_usage(with_tokens: int, total: int, total_tokens: float) -> Evidence | None:
    # Opt-in: only when the agent actually logs token counts in `data`.
    if with_tokens == 0 or total == 0:
        return None
    coverage = round(100 * with_tokens / total, 1)
    return Evidence(
        id="token_usage",
        category="token_optimization",
        label="Token usage",
        summary=f"{int(total_tokens):,} tokens recorded across {with_tokens} events "
        f"({coverage}% of events carry token data).",
        metrics={"total_tokens": int(total_tokens), "coverage_pct": coverage, "events_with_tokens": with_tokens},
        strength=_scale(coverage, 10, 100),
    )


def _detect_high_latency(with_latency: int, total: int, avg_latency: float, max_latency: float) -> Evidence | None:
    if with_latency == 0 or avg_latency < _THRESH["high_latency_ms"]:
        return None
    coverage = round(100 * with_latency / total, 1) if total else 0
    return Evidence(
        id="high_latency",
        category="performance",
        label="High latency",
        summary=f"Average logged latency is {int(avg_latency)}ms (max {int(max_latency)}ms), "
        f"across {coverage}% of events that record it.",
        metrics={"avg_latency_ms": int(avg_latency), "max_latency_ms": int(max_latency), "coverage_pct": coverage},
        strength=_scale(avg_latency, _THRESH["high_latency_ms"], 20000),
    )


# ── DB orchestration ───────────────────────────────────────────────────────


async def extract_evidence(
    pool, tenant_id: str, agent_id: str, from_dt: datetime, to_dt: datetime
) -> EvidenceBundle:
    summary = await reports._summary(pool, tenant_id, agent_id, from_dt, to_dt)
    period = {"from": from_dt.replace(microsecond=0).isoformat(), "to": to_dt.replace(microsecond=0).isoformat(), "days": (to_dt - from_dt).days}

    # Too little data to say anything meaningful → empty bundle → no Claude call.
    if (summary.get("total_events") or 0) < _THRESH["min_events"]:
        return EvidenceBundle(agent=agent_id, period=period, summary=summary, evidence=[], coverage={})

    dist = await reports.window_metrics(pool, tenant_id, agent_id, from_dt, to_dt)
    sessions = await reports._sessions(pool, tenant_id, agent_id, from_dt, to_dt)
    drift = await reports._drift(pool, tenant_id, agent_id, from_dt, to_dt)

    recurring_rows = await _fetch_recurring_errors(pool, tenant_id, agent_id, from_dt, to_dt)
    dup_rows = await _fetch_retry_loops(pool, tenant_id, agent_id, from_dt, to_dt)
    chain = await _fetch_chain_depth(pool, tenant_id, agent_id, from_dt, to_dt)
    missing = await _fetch_missing_sessions(pool, tenant_id, agent_id, from_dt, to_dt)
    tokens = await _fetch_token_stats(pool, tenant_id, agent_id, from_dt, to_dt)
    latency = await _fetch_latency_stats(pool, tenant_id, agent_id, from_dt, to_dt)

    slow = [s for s in sessions if (s.get("duration_seconds") or 0) > _THRESH["slow_session_seconds"]]

    detected = [
        _detect_low_success_rate(summary),
        _detect_recurring_errors(recurring_rows),
        _detect_retry_loops(dup_rows),
        _detect_long_chains(chain["max_depth"], chain["avg_depth"]),
        _detect_missing_sessions(missing["null_count"], missing["total"]),
        _detect_low_diversity(summary),
        _detect_slow_sessions(slow, len(sessions)),
        _detect_drift(drift),
        _detect_sparse_activity(summary),
        _detect_token_usage(tokens["with"], tokens["total"], tokens["sum"]),
        _detect_high_latency(latency["with"], latency["total"], latency["avg"], latency["max"]),
    ]
    evidence = [e for e in detected if e is not None]

    coverage = {
        "tokens": {"events": tokens["with"], "pct": round(100 * tokens["with"] / tokens["total"], 1) if tokens["total"] else 0},
        "latency": {"events": latency["with"], "pct": round(100 * latency["with"] / latency["total"], 1) if latency["total"] else 0},
    }
    summary_facts = {
        "total_events": summary.get("total_events"),
        "unique_event_types": summary.get("unique_event_types"),
        "sessions": summary.get("sessions"),
        "error_rate_pct": summary.get("error_rate_pct"),
        "top_event_types": {k: v for k, v in list(dist.get("event_distribution", {}).items())[:8]},
    }
    return EvidenceBundle(agent=agent_id, period=period, summary=summary_facts, evidence=evidence, coverage=coverage)


# ── focused SQL (kept small; all parameterised) ────────────────────────────


async def _fetch_recurring_errors(pool, tenant_id, agent_id, from_dt, to_dt):
    rows = await pool.fetch(
        f"""
        SELECT COALESCE(NULLIF(data->>'error', ''), event_type) AS error_key, COUNT(*) AS count
        FROM events
        WHERE tenant_id = $1 AND agent_id = $2 AND timestamp >= $3 AND timestamp < $4
          AND ({reports._ERROR_PREDICATE})
        GROUP BY error_key
        ORDER BY count DESC
        LIMIT 10
        """,
        tenant_id, agent_id, from_dt, to_dt,
    )
    return [{"error_key": r["error_key"], "count": r["count"]} for r in rows]


async def _fetch_retry_loops(pool, tenant_id, agent_id, from_dt, to_dt):
    rows = await pool.fetch(
        """
        WITH ordered AS (
            SELECT session_id, event_type,
                   LAG(event_type) OVER (PARTITION BY session_id ORDER BY sequence_no) AS prev_type
            FROM events
            WHERE tenant_id = $1 AND agent_id = $2 AND session_id IS NOT NULL
              AND timestamp >= $3 AND timestamp < $4
        )
        SELECT event_type, COUNT(*) AS repeat_count
        FROM ordered
        WHERE event_type = prev_type
        GROUP BY event_type
        ORDER BY repeat_count DESC
        LIMIT 10
        """,
        tenant_id, agent_id, from_dt, to_dt,
    )
    return [{"event_type": r["event_type"], "repeat_count": r["repeat_count"]} for r in rows]


async def _fetch_chain_depth(pool, tenant_id, agent_id, from_dt, to_dt):
    row = await pool.fetchrow(
        """
        WITH RECURSIVE chain AS (
            SELECT s.event_id, 1 AS depth
            FROM events s
            WHERE s.tenant_id = $1 AND s.agent_id = $2
              AND s.timestamp >= $3 AND s.timestamp < $4
              -- Chain starts: no parent, or a parent that was erased (forget
              -- and agent delete keep the child's parent_event_id).
              AND (
                  s.parent_event_id IS NULL
                  OR NOT EXISTS (SELECT 1 FROM events p WHERE p.event_id = s.parent_event_id)
              )
            UNION ALL
            SELECT e.event_id, c.depth + 1
            FROM events e
            JOIN chain c ON e.parent_event_id = c.event_id
            WHERE e.tenant_id = $1 AND e.agent_id = $2 AND c.depth < 200
        )
        SELECT COALESCE(MAX(depth), 0) AS max_depth, COALESCE(AVG(depth), 0)::float AS avg_depth
        FROM chain
        """,
        tenant_id, agent_id, from_dt, to_dt,
    )
    return {"max_depth": row["max_depth"] or 0, "avg_depth": row["avg_depth"] or 0.0}


async def _fetch_missing_sessions(pool, tenant_id, agent_id, from_dt, to_dt):
    row = await pool.fetchrow(
        """
        SELECT COUNT(*) FILTER (WHERE session_id IS NULL) AS null_count, COUNT(*) AS total
        FROM events
        WHERE tenant_id = $1 AND agent_id = $2 AND timestamp >= $3 AND timestamp < $4
        """,
        tenant_id, agent_id, from_dt, to_dt,
    )
    return {"null_count": row["null_count"] or 0, "total": row["total"] or 0}


_NUM_RE = "^[0-9]+(\\.[0-9]+)?$"


async def _fetch_token_stats(pool, tenant_id, agent_id, from_dt, to_dt):
    # Opt-in: numeric token counts logged under common keys in `data`.
    row = await pool.fetchrow(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE data ? 'total_tokens' OR data ? 'tokens' OR data ? 'usage') AS with_tokens,
            COUNT(*) AS total,
            COALESCE(SUM(
                CASE
                    WHEN data->>'total_tokens' ~ '{_NUM_RE}' THEN (data->>'total_tokens')::numeric
                    WHEN data->>'tokens' ~ '{_NUM_RE}' THEN (data->>'tokens')::numeric
                    WHEN data->'usage'->>'total_tokens' ~ '{_NUM_RE}' THEN (data->'usage'->>'total_tokens')::numeric
                    ELSE 0
                END
            ), 0)::float AS sum_tokens
        FROM events
        WHERE tenant_id = $1 AND agent_id = $2 AND timestamp >= $3 AND timestamp < $4
        """,
        tenant_id, agent_id, from_dt, to_dt,
    )
    return {"with": row["with_tokens"] or 0, "total": row["total"] or 0, "sum": row["sum_tokens"] or 0.0}


async def _fetch_latency_stats(pool, tenant_id, agent_id, from_dt, to_dt):
    row = await pool.fetchrow(
        f"""
        WITH vals AS (
            SELECT CASE
                WHEN data->>'latency_ms' ~ '{_NUM_RE}' THEN (data->>'latency_ms')::numeric
                WHEN data->>'duration_ms' ~ '{_NUM_RE}' THEN (data->>'duration_ms')::numeric
                ELSE NULL
            END AS ms
            FROM events
            WHERE tenant_id = $1 AND agent_id = $2 AND timestamp >= $3 AND timestamp < $4
        )
        SELECT COUNT(ms) AS with_latency,
               (SELECT COUNT(*) FROM vals) AS total,
               COALESCE(AVG(ms), 0)::float AS avg_ms,
               COALESCE(MAX(ms), 0)::float AS max_ms
        FROM vals
        """,
        tenant_id, agent_id, from_dt, to_dt,
    )
    return {
        "with": row["with_latency"] or 0,
        "total": row["total"] or 0,
        "avg": row["avg_ms"] or 0.0,
        "max": row["max_ms"] or 0.0,
    }
