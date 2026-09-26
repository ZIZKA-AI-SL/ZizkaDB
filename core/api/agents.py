import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from services.exceptions import bad_request, conflict, not_found, service_unavailable
from pydantic import BaseModel, Field

from api.deps import (
    assert_agent_allowed,
    get_tenant,
    is_unbound_api_key,
    require_dashboard_session,
)
from db.connection import get_pool, get_redis, get_qdrant, QDRANT_COLLECTION
from services.rate_limiter import (
    InMemoryStorage,
    RateLimiter,
    RateLimitStorage,
    RedisStorage,
    SlidingWindowStrategy,
    check_rate_fail_open,
)
from services import reports, token_optimization, token_usage
from services import token_optimization_config
from services.ai import config as ai_config
from services.ai.provider import AIProviderError
from services.suggestions import engine as suggestions_engine
from services.api_keys import (
    assert_and_reserve_api_key_slot,
    create_api_key_record,
    list_api_keys_for_agent,
    revoke_api_key_record,
)
from services.event_write import write_event

router = APIRouter()
log = logging.getLogger(__name__)

_AGENT_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,254}$")

# Per-tenant throttle for the AI suggestions endpoint. Cache + Redis lock already
# coalesce repeat reads, but `refresh=true` bypasses both and triggers a live
# (paid, ~20s) Claude call, so cap how often a tenant can force regeneration.
_SUGGESTIONS_REFRESH_WINDOW_SEC = 3600
_SUGGESTIONS_REFRESH_MAX = 20
_SUGGESTIONS_RATE_WINDOW_SEC = 60
_SUGGESTIONS_RATE_MAX = 30

_suggestions_fallback_storage = InMemoryStorage()


def _suggestions_storage() -> RateLimitStorage:
    """
    Choose suggestions rate-limit storage.

    SUGGESTIONS_RATE_LIMIT_STORAGE=redis|memory overrides the default.
    Default: redis when ENV=production (shared across uvicorn workers),
    memory otherwise (local dev + unit tests).
    """
    choice = (os.getenv("SUGGESTIONS_RATE_LIMIT_STORAGE") or "").strip().lower()
    if not choice:
        choice = (
            "redis" if os.getenv("ENV", "development") == "production" else "memory"
        )
    if choice == "redis":
        return RedisStorage(key_prefix="suggestions")
    if choice == "memory":
        return InMemoryStorage()
    log.warning(
        "Unknown SUGGESTIONS_RATE_LIMIT_STORAGE=%r; falling back to memory",
        choice,
    )
    return InMemoryStorage()


suggestions_refresh_limiter = RateLimiter(
    limit=_SUGGESTIONS_REFRESH_MAX,
    window_sec=_SUGGESTIONS_REFRESH_WINDOW_SEC,
    storage=_suggestions_storage(),
    strategy=SlidingWindowStrategy(),
    detail="Suggestion regeneration limit reached. Try again later.",
)

suggestions_rate_limiter = RateLimiter(
    limit=_SUGGESTIONS_RATE_MAX,
    window_sec=_SUGGESTIONS_RATE_WINDOW_SEC,
    storage=_suggestions_storage(),
    strategy=SlidingWindowStrategy(),
    detail="Too many suggestion requests. Try again shortly.",
)

suggestions_refresh_fallback = RateLimiter(
    limit=_SUGGESTIONS_REFRESH_MAX,
    window_sec=_SUGGESTIONS_REFRESH_WINDOW_SEC,
    storage=_suggestions_fallback_storage,
    strategy=SlidingWindowStrategy(),
    detail="Suggestion regeneration limit reached. Try again later.",
)

suggestions_rate_fallback = RateLimiter(
    limit=_SUGGESTIONS_RATE_MAX,
    window_sec=_SUGGESTIONS_RATE_WINDOW_SEC,
    storage=_suggestions_fallback_storage,
    strategy=SlidingWindowStrategy(),
    detail="Too many suggestion requests. Try again shortly.",
)


async def _check_suggestions_rate(
    limiter: RateLimiter, fallback: RateLimiter, key: str
) -> None:
    """Check tenant throttle; fail-open to per-worker memory when Redis is down."""
    await check_rate_fail_open(
        limiter, fallback, key, context="suggestions rate limit"
    )


def _validate_agent_id(agent_id: str) -> str:
    agent_id = agent_id.strip()
    if not agent_id or not _AGENT_ID_RE.match(agent_id):
        raise bad_request(
            "agent_id must be 1–255 chars: letters, numbers, dots, dashes, underscores"
        )
    return agent_id


class CreateAgentBody(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=255)
    metadata: dict | None = None
    key_name: str | None = None


class CreateAgentKeyBody(BaseModel):
    name: str | None = None


@router.get("")
async def list_agents(tenant: dict = Depends(get_tenant)):
    # A key not yet bound to an agent owns no agents — never list the tenant.
    if is_unbound_api_key(tenant):
        return []
    pool = get_pool()
    scoped_agent = tenant.get("agent_id")
    agent_filter = ""
    params: list = [tenant["tenant_id"]]
    if scoped_agent:
        agent_filter = "AND a.agent_id = $2"
        params.append(scoped_agent)

    rows = await pool.fetch(
        f"""
        SELECT
            a.agent_id,
            a.first_seen,
            a.last_seen,
            a.event_count,
            a.metadata,
            COUNT(ak.key_id) FILTER (WHERE ak.revoked = FALSE) AS api_key_count
        FROM agents a
        LEFT JOIN api_keys ak
            ON ak.tenant_id = a.tenant_id AND ak.agent_id = a.agent_id
        WHERE a.tenant_id = $1
        {agent_filter}
        GROUP BY a.agent_id, a.first_seen, a.last_seen, a.event_count, a.metadata
        ORDER BY a.last_seen DESC
        """,
        *params,
    )
    return [
        {
            "agent": r["agent_id"],
            "first_seen": r["first_seen"].isoformat(),
            "last_seen": r["last_seen"].isoformat(),
            "event_count": r["event_count"],
            "api_key_count": int(r["api_key_count"] or 0),
        }
        for r in rows
    ]


@router.post("", status_code=201)
async def create_agent(
    body: CreateAgentBody,
    session: dict = Depends(require_dashboard_session),
):
    """Create an agent and its first API key (shown once).

    Agent + first key are created in one transaction so a plan-limit rejection
    (the auto-created key counts against the quota) rolls back the agent instead
    of leaving an agent with no key.
    """
    agent_id = _validate_agent_id(body.agent_id)
    pool = get_pool()
    tenant_id = session["tenant_id"]

    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT 1 FROM agents WHERE tenant_id = $1 AND agent_id = $2",
                tenant_id, agent_id,
            )
            if existing:
                raise conflict("Agent already exists")

            await assert_and_reserve_api_key_slot(conn, tenant_id=tenant_id)

            await conn.execute(
                """
                INSERT INTO agents (agent_id, tenant_id, metadata)
                VALUES ($1, $2, $3::jsonb)
                """,
                agent_id, tenant_id, json.dumps(body.metadata or {}),
            )

            key_name = body.key_name or f"{agent_id} production"
            api_key = await create_api_key_record(
                conn,
                tenant_id=tenant_id,
                name=key_name,
                agent_id=agent_id,
            )

    return {
        "agent": agent_id,
        "api_key": api_key,
        "message": "Agent and API key created. Save the key now — use it as ZIZKADB_API_KEY or AGENTDB_API_KEY.",
    }


async def _purge_agent_vectors(tenant_id: str, agent_id: str) -> None:
    try:
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

        qdrant = get_qdrant()
        await qdrant.delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[
                        FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
                        FieldCondition(key="agent_id", match=MatchValue(value=agent_id)),
                    ]
                )
            ),
        )
    except Exception as e:
        log.warning(
            "Qdrant purge for agent %s (tenant %s) failed: %s",
            agent_id,
            tenant_id,
            e,
        )


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    tenant: dict = Depends(require_dashboard_session),
):
    """Delete an agent and all of its events. Cannot be undone."""
    agent_id = _validate_agent_id(agent_id)
    pool = get_pool()
    tenant_id = tenant["tenant_id"]

    row = await pool.fetchrow(
        "SELECT event_count FROM agents WHERE tenant_id = $1 AND agent_id = $2",
        tenant_id, agent_id,
    )
    if not row:
        raise not_found("Agent not found")

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE events SET parent_event_id = NULL WHERE tenant_id = $1 AND agent_id = $2",
                tenant_id, agent_id,
            )
            await conn.execute(
                "DELETE FROM events WHERE tenant_id = $1 AND agent_id = $2",
                tenant_id, agent_id,
            )
            await conn.execute(
                "DELETE FROM agents WHERE tenant_id = $1 AND agent_id = $2",
                tenant_id, agent_id,
            )

    await _purge_agent_vectors(tenant_id, agent_id)

    return {
        "deleted": True,
        "agent": agent_id,
        "events_deleted": int(row["event_count"] or 0),
    }


@router.post("/{agent_id}/test-event", status_code=201)
async def test_agent_event(
    agent_id: str,
    tenant: dict = Depends(get_tenant),
):
    """Log a test event to this agent (dashboard JWT). Verifies the pipeline for this agent."""
    agent_id = _validate_agent_id(agent_id)
    await assert_agent_allowed(tenant, agent_id)
    pool = get_pool()
    tenant_id = tenant["tenant_id"]

    exists = await pool.fetchrow(
        "SELECT 1 FROM agents WHERE tenant_id = $1 AND agent_id = $2",
        tenant_id, agent_id,
    )
    if not exists:
        raise not_found("Agent not found")

    result = await write_event(
        tenant_id=tenant_id,
        agent=agent_id,
        event="connection_test",
        data={"source": "dashboard_agent_page", "ok": True},
    )
    return {
        **result,
        "agent": agent_id,
        "message": f"Test event recorded for '{agent_id}'. Refresh Events — it should appear within seconds.",
    }


@router.get("/{agent_id}/api-keys")
async def list_agent_api_keys(
    agent_id: str,
    session: dict = Depends(require_dashboard_session),
):
    # Key management is dashboard-only (JWT); an API key — especially a scoped
    # agent key — must never enumerate keys. Mirrors create/revoke.
    agent_id = _validate_agent_id(agent_id)
    pool = get_pool()
    tenant_id = session["tenant_id"]

    exists = await pool.fetchrow(
        "SELECT 1 FROM agents WHERE tenant_id = $1 AND agent_id = $2",
        tenant_id, agent_id,
    )
    if not exists:
        raise not_found("Agent not found")

    return await list_api_keys_for_agent(pool, tenant_id, agent_id)


@router.post("/{agent_id}/api-keys", status_code=201)
async def create_agent_api_key(
    agent_id: str,
    body: CreateAgentKeyBody,
    session: dict = Depends(require_dashboard_session),
):
    agent_id = _validate_agent_id(agent_id)
    pool = get_pool()
    tenant_id = session["tenant_id"]

    async with pool.acquire() as conn:
        async with conn.transaction():
            exists = await conn.fetchrow(
                "SELECT 1 FROM agents WHERE tenant_id = $1 AND agent_id = $2",
                tenant_id, agent_id,
            )
            if not exists:
                raise not_found("Agent not found")

            await assert_and_reserve_api_key_slot(conn, tenant_id=tenant_id)

            return await create_api_key_record(
                conn,
                tenant_id=tenant_id,
                name=body.name or f"{agent_id} key",
                agent_id=agent_id,
            )


@router.delete("/{agent_id}/api-keys/{key_id}")
async def revoke_agent_api_key(
    agent_id: str,
    key_id: str,
    session: dict = Depends(require_dashboard_session),
):
    # Key management is dashboard-only (JWT); an API key must never revoke keys.
    agent_id = _validate_agent_id(agent_id)
    pool = get_pool()
    tenant_id = session["tenant_id"]

    if not await revoke_api_key_record(pool, tenant_id, key_id, agent_id):
        raise not_found("API key not found")
    return {"revoked": True, "key_id": key_id, "agent": agent_id}


@router.get("/{agent_id}/stats")
async def agent_stats(agent_id: str, tenant: dict = Depends(get_tenant)):
    agent_id = _validate_agent_id(agent_id)
    await assert_agent_allowed(tenant, agent_id)
    pool = get_pool()

    stats = await pool.fetchrow(
        """
        SELECT
            COUNT(*) AS total_events,
            COUNT(DISTINCT event_type) AS unique_event_types,
            COUNT(DISTINCT session_id) AS sessions,
            MIN(timestamp) AS first_event,
            MAX(timestamp) AS last_event
        FROM events
        WHERE tenant_id = $1 AND agent_id = $2
        """,
        tenant["tenant_id"], agent_id,
    )

    top_events = await pool.fetch(
        """
        SELECT event_type, COUNT(*) AS count
        FROM events
        WHERE tenant_id = $1 AND agent_id = $2
        GROUP BY event_type
        ORDER BY count DESC
        LIMIT 10
        """,
        tenant["tenant_id"], agent_id,
    )

    return {
        "agent": agent_id,
        "total_events": stats["total_events"],
        "unique_event_types": stats["unique_event_types"],
        "sessions": stats["sessions"],
        "first_event": stats["first_event"].isoformat() if stats["first_event"] else None,
        "last_event": stats["last_event"].isoformat() if stats["last_event"] else None,
        "top_events": [{"event": r["event_type"], "count": r["count"]} for r in top_events],
    }


@router.get("/{agent_id}/report")
async def agent_report(
    agent_id: str,
    from_: str = Query(..., alias="from", description="ISO-8601 start of the report window"),
    to: str = Query(..., description="ISO-8601 end of the report window (exclusive)"),
    granularity: str | None = Query(default=None, pattern=r"^(day|week)$"),
    tenant: dict = Depends(get_tenant),
):
    """Single consolidated, date-ranged report for one agent.

    Composes summary KPIs (with previous-period deltas), a gap-filled events/
    errors time series, event breakdown, sessions, behavior drift and rule-based
    recommendations from real events. See services/reports.py.
    """
    agent_id = _validate_agent_id(agent_id)
    await assert_agent_allowed(tenant, agent_id)

    try:
        from_dt = reports.parse_utc_naive(from_, "from")
        to_dt = reports.parse_utc_naive(to, "to")
        reports.validate_range(from_dt, to_dt)
    except ValueError as exc:
        raise bad_request(detail=str(exc))

    granularity = reports.resolve_granularity(from_dt, to_dt, granularity)
    pool = get_pool()
    return await reports.build_report(
        pool, tenant["tenant_id"], agent_id, from_dt, to_dt, granularity
    )


@router.get("/{agent_id}/token-usage")
async def agent_token_usage(
    agent_id: str,
    from_: str = Query(..., alias="from", description="ISO-8601 start of the report window"),
    to: str = Query(..., description="ISO-8601 end of the report window (exclusive)"),
    granularity: str | None = Query(default=None, pattern=r"^(hour|day|week)$"),
    tenant: dict = Depends(get_tenant),
):
    """Token usage / cost report for one agent, aggregated from real events.

    Reads the `token_usage` convention key from `events.data` JSONB (see
    services/token_usage.py and docs/adr/ for the schema) — no new table, no
    schema migration. Cost is computed server-side from a hardcoded pricing
    map (services/pricing.py); unknown models contribute $0 cost and are
    listed in `unpriced_models` rather than silently under-reported.

    Note: this endpoint accepts `granularity=hour` (for the 24h preset) in
    addition to day/week — intentionally wider than /report's pattern, which
    is left unchanged.
    """
    agent_id = _validate_agent_id(agent_id)
    await assert_agent_allowed(tenant, agent_id)

    try:
        from_dt = reports.parse_utc_naive(from_, "from")
        to_dt = reports.parse_utc_naive(to, "to")
        reports.validate_range(from_dt, to_dt)
    except ValueError as exc:
        raise bad_request(detail=str(exc))

    if granularity not in (None, "hour", "day", "week"):
        raise bad_request(detail="granularity must be one of hour, day, week")
    if granularity is None:
        granularity = reports.resolve_granularity(from_dt, to_dt, None)
    elif granularity == "hour" and (to_dt - from_dt).days > 7:
        # An explicit `hour` request on a long range would gap-fill thousands of
        # buckets (up to 366*24). Cap it the same way resolve_granularity caps an
        # explicit `day` request on a too-long range, rather than trusting the
        # caller's requested granularity unconditionally.
        granularity = reports.resolve_granularity(from_dt, to_dt, None)

    pool = get_pool()
    return await token_usage.build_token_usage_report(
        pool, tenant["tenant_id"], agent_id, from_dt, to_dt, granularity
    )


@router.get("/{agent_id}/token-optimization")
async def agent_token_optimization(
    agent_id: str,
    from_: str | None = Query(default=None, alias="from", description="ISO-8601 window start"),
    to: str | None = Query(default=None, description="ISO-8601 window end (exclusive)"),
    granularity: str | None = Query(default=None, pattern=r"^(day|week)$"),
    tenant: dict = Depends(get_tenant),
):
    """Deterministic, non-AI Token Optimization suggestions for one agent.

    100% computed from real `token_usage`-bearing events (no LLM call) — see
    services/token_optimization.py and docs/adr/007-deterministic-token-optimization.md.
    Every $/% number is a real, reproducible computation, never AI-estimated.

    Defaults to a 30-day window when `from`/`to` are omitted (matches
    /suggestions' default — optimization needs enough history to be
    meaningful). No AI rate limiting (no external API call, same reasoning
    already documented for /token-usage). No caching in v1.
    """
    agent_id = _validate_agent_id(agent_id)
    await assert_agent_allowed(tenant, agent_id)

    try:
        if from_ is None or to is None:
            to_dt = datetime.now(timezone.utc).replace(tzinfo=None)
            from_dt = to_dt - timedelta(days=token_optimization_config.DEFAULT_WINDOW_DAYS)
        else:
            from_dt = reports.parse_utc_naive(from_, "from")
            to_dt = reports.parse_utc_naive(to, "to")
        reports.validate_range(from_dt, to_dt)
    except ValueError as exc:
        raise bad_request(detail=str(exc))

    granularity = reports.resolve_granularity(from_dt, to_dt, granularity)
    pool = get_pool()
    return await token_optimization.build_token_optimization_report(
        pool, tenant["tenant_id"], agent_id, from_dt, to_dt, granularity
    )


@router.get("/{agent_id}/suggestions")
async def agent_suggestions(
    agent_id: str,
    from_: str | None = Query(default=None, alias="from", description="ISO-8601 window start"),
    to: str | None = Query(default=None, description="ISO-8601 window end (exclusive)"),
    refresh: bool = Query(default=False, description="Bypass the cache and regenerate"),
    tenant: dict = Depends(get_tenant),
):
    """AI-generated, evidence-grounded improvement suggestions for one agent.

    Deterministic signals are extracted from real events (see services/
    suggestions/evidence.py); Claude only elaborates on those supplied facts and
    a validation layer drops anything ungrounded. Returns status ``ok`` /
    ``no_evidence`` / ``ai_not_configured``. Never invents recommendations.
    """
    agent_id = _validate_agent_id(agent_id)
    await assert_agent_allowed(tenant, agent_id)

    # Throttle per tenant to bound AI spend. `refresh` bypasses the cache/lock
    # and forces a live Claude call, so it gets a much tighter cap.
    tenant_key = tenant["tenant_id"]
    if refresh:
        await _check_suggestions_rate(
            suggestions_refresh_limiter, suggestions_refresh_fallback, tenant_key
        )
    await _check_suggestions_rate(
        suggestions_rate_limiter, suggestions_rate_fallback, tenant_key
    )

    # Default to the last 30 days when the caller omits an explicit window.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        to_dt = reports.parse_utc_naive(to, "to") if to else now
        from_dt = reports.parse_utc_naive(from_, "from") if from_ else to_dt - timedelta(days=30)
        reports.validate_range(from_dt, to_dt)
    except ValueError as exc:
        raise bad_request(detail=str(exc))

    if not ai_config.ai_configured():
        return {
            "agent": agent_id,
            "status": "ai_not_configured",
            "period": {"from": from_dt.replace(microsecond=0).isoformat(), "to": to_dt.replace(microsecond=0).isoformat(), "days": (to_dt - from_dt).days},
            "model": None,
            "generated_at": now.replace(microsecond=0).isoformat() + "Z",
            "suggestions": [],
            "evidence": [],
            "meta": {},
        }

    try:
        result = await suggestions_engine.get_suggestions(
            get_pool(), get_redis(), tenant["tenant_id"], agent_id, from_dt, to_dt, refresh=refresh
        )
    except AIProviderError as exc:
        raise service_unavailable(detail=f"AI analysis is temporarily unavailable: {exc}")
    return result.to_dict()


@router.get("/{agent_id}/sessions")
async def list_sessions(
    agent_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    tenant: dict = Depends(get_tenant),
):
    agent_id = _validate_agent_id(agent_id)
    await assert_agent_allowed(tenant, agent_id)
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT
            session_id,
            COUNT(*)                    AS event_count,
            COUNT(DISTINCT event_type)  AS event_types,
            MIN(timestamp)              AS started_at,
            MAX(timestamp)              AS ended_at,
            EXTRACT(EPOCH FROM (MAX(timestamp) - MIN(timestamp)))::int AS duration_seconds,
            array_agg(DISTINCT event_type ORDER BY event_type) AS types
        FROM events
        WHERE tenant_id = $1
          AND agent_id  = $2
          AND session_id IS NOT NULL
        GROUP BY session_id
        ORDER BY MAX(timestamp) DESC
        LIMIT $3
        """,
        tenant["tenant_id"], agent_id, limit,
    )
    return [
        {
            "session_id":       r["session_id"],
            "event_count":      r["event_count"],
            "event_types":      r["event_types"],
            "started_at":       r["started_at"].isoformat(),
            "ended_at":         r["ended_at"].isoformat(),
            "duration_seconds": r["duration_seconds"] or 0,
            "types":            list(r["types"]),
        }
        for r in rows
    ]


# ── Behavioral baseline ────────────────────────────────────────────────────
#
# A behavioral baseline summarises what is "normal" for an agent across three
# dimensions:
#   1. Event-type distribution        — what does this agent typically emit?
#   2. Parent → child transitions     — what does its decision-tree shape look like?
#   3. Session shape                  — events per session, duration, error rate
#
# We compute the same three things for the most recent 50 sessions and compare
# them against everything before. The result is a drift score plus a list of
# the biggest behavioural changes between the two windows.
#
# This is deliberately observable, not alerting. The dashboard renders the
# baseline so a developer can build trust in it before we wire alerts on top.


def _distribution(rows, key: str) -> dict[str, float]:
    """Convert a list of {key: ..., 'count': N} rows into a percentage dict."""
    total = sum(r["count"] for r in rows) or 1
    return {r[key]: round(100 * r["count"] / total, 2) for r in rows}


def _l1_distance(a: dict[str, float], b: dict[str, float]) -> float:
    """L1 distance between two distributions, normalised to [0, 1]."""
    keys = set(a) | set(b)
    diff = sum(abs(a.get(k, 0) - b.get(k, 0)) for k in keys)
    return round(diff / 200, 4)  # max possible is 200pp -> 1.0


def _biggest_changes(
    label: str, baseline: dict[str, float], recent: dict[str, float], top: int = 5,
) -> list[dict]:
    keys = set(baseline) | set(recent)
    deltas = []
    for k in keys:
        b = baseline.get(k, 0)
        r = recent.get(k, 0)
        deltas.append({
            "metric":   f"{label}.{k}",
            "baseline": b,
            "recent":   r,
            "delta_pp": round(r - b, 2),
        })
    deltas.sort(key=lambda d: abs(d["delta_pp"]), reverse=True)
    return [d for d in deltas[:top] if d["delta_pp"] != 0]


async def _baseline_for(
    pool, tenant_id, agent_id: str, session_ids: list[str],
) -> dict:
    """Compute baseline metrics over the given list of session_ids."""
    if not session_ids:
        return {
            "sessions":               0,
            "events":                 0,
            "event_distribution":     {},
            "transitions":            {},
            "avg_events_per_session": 0,
            "avg_session_seconds":    0,
            "error_rate_pct":         0,
        }

    type_rows = await pool.fetch(
        """
        SELECT event_type AS key, COUNT(*) AS count
        FROM events
        WHERE tenant_id = $1 AND agent_id = $2 AND session_id = ANY($3::text[])
        GROUP BY event_type
        """,
        tenant_id, agent_id, session_ids,
    )

    transition_rows = await pool.fetch(
        """
        SELECT
            p.event_type || ' -> ' || c.event_type AS key,
            COUNT(*) AS count
        FROM events c
        JOIN events p ON c.parent_event_id = p.event_id AND p.tenant_id = c.tenant_id
        WHERE c.tenant_id = $1 AND c.agent_id = $2 AND c.session_id = ANY($3::text[])
        GROUP BY p.event_type, c.event_type
        ORDER BY count DESC
        LIMIT 20
        """,
        tenant_id, agent_id, session_ids,
    )

    session_stats = await pool.fetchrow(
        """
        WITH s AS (
            SELECT
                session_id,
                COUNT(*) AS events,
                EXTRACT(EPOCH FROM (MAX(timestamp) - MIN(timestamp)))::int AS dur
            FROM events
            WHERE tenant_id = $1 AND agent_id = $2 AND session_id = ANY($3::text[])
            GROUP BY session_id
        )
        SELECT
            AVG(events)::float AS avg_events,
            AVG(dur)::float    AS avg_dur
        FROM s
        """,
        tenant_id, agent_id, session_ids,
    )

    error_rate = await pool.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (
                WHERE event_type ILIKE '%error%'
                   OR event_type ILIKE '%fail%'
                   OR data ? 'error'
            )::float * 100.0 / NULLIF(COUNT(*), 0) AS pct
        FROM events
        WHERE tenant_id = $1 AND agent_id = $2 AND session_id = ANY($3::text[])
        """,
        tenant_id, agent_id, session_ids,
    )

    total_events = sum(r["count"] for r in type_rows)
    return {
        "sessions":               len(session_ids),
        "events":                 total_events,
        "event_distribution":     _distribution(type_rows, "key"),
        "transitions":            _distribution(transition_rows, "key"),
        "avg_events_per_session": round(session_stats["avg_events"] or 0, 2),
        "avg_session_seconds":    round(session_stats["avg_dur"] or 0, 1),
        "error_rate_pct":         round(error_rate["pct"] or 0, 2),
    }


@router.get("/{agent_id}/baseline")
async def agent_baseline(
    agent_id: str,
    recent_window: int = Query(default=50, ge=5, le=500),
    window: str | None = Query(default=None, pattern=r"^(24h|7d|30d)$"),
    tenant: dict = Depends(get_tenant),
):
    """
    Behavioral baseline for an agent.

    When `window` is provided (24h / 7d / 30d) the split is time-based:
    `recent` = sessions ending inside that window, `baseline` = everything before.
    Otherwise falls back to the most recent N sessions vs everything prior.
    """
    agent_id = _validate_agent_id(agent_id)
    await assert_agent_allowed(tenant, agent_id)
    pool = get_pool()

    sessions = await pool.fetch(
        """
        SELECT session_id, MAX(timestamp) AS ended_at
        FROM events
        WHERE tenant_id = $1 AND agent_id = $2 AND session_id IS NOT NULL
        GROUP BY session_id
        ORDER BY MAX(timestamp) DESC
        """,
        tenant["tenant_id"], agent_id,
    )

    if not sessions:
        return {
            "agent":         agent_id,
            "status":        "insufficient_data",
            "message":       "No sessions logged yet. Add session_id to your db.log() calls to start building a baseline.",
            "sessions":      0,
            "recent_window": recent_window,
        }

    # Time-based split when a window param is supplied
    if window:
        delta = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}[window]
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - delta
        recent_ids   = [r["session_id"] for r in sessions if r["ended_at"] >= cutoff]
        baseline_ids = [r["session_id"] for r in sessions if r["ended_at"] <  cutoff]
    else:
        recent_ids   = [r["session_id"] for r in sessions[:recent_window]]
        baseline_ids = [r["session_id"] for r in sessions[recent_window:]]

    if not baseline_ids:
        return {
            "agent":         agent_id,
            "status":        "warming_up",
            "message":       f"Need at least {recent_window + 1} sessions to compute drift. You have {len(sessions)}. Keep logging.",
            "sessions":      len(sessions),
            "recent_window": recent_window,
            "recent":        await _baseline_for(pool, tenant["tenant_id"], agent_id, recent_ids),
        }

    baseline = await _baseline_for(pool, tenant["tenant_id"], agent_id, baseline_ids)
    recent   = await _baseline_for(pool, tenant["tenant_id"], agent_id, recent_ids)

    drift_score = round(
        0.5 * _l1_distance(baseline["event_distribution"], recent["event_distribution"])
        + 0.5 * _l1_distance(baseline["transitions"],         recent["transitions"]),
        4,
    )

    biggest_changes = (
        _biggest_changes("event_distribution", baseline["event_distribution"], recent["event_distribution"])
        + _biggest_changes("transitions",      baseline["transitions"],         recent["transitions"])
    )
    biggest_changes.sort(key=lambda d: abs(d["delta_pp"]), reverse=True)

    if drift_score < 0.05:
        verdict = "stable"
    elif drift_score < 0.15:
        verdict = "minor_drift"
    elif drift_score < 0.30:
        verdict = "noticeable_drift"
    else:
        verdict = "significant_drift"

    return {
        "agent":         agent_id,
        "status":        "ok",
        "sessions":      len(sessions),
        "recent_window": recent_window,
        "window":        window,
        "baseline":      baseline,
        "recent":        recent,
        "drift": {
            "score":                     drift_score,
            "behavior_change_pct":       round(drift_score * 100, 1),
            "verdict":                   verdict,
            "biggest_changes":           biggest_changes[:8],
            "behavior_change_pp":        round(recent["error_rate_pct"] - baseline["error_rate_pct"], 2),
            "error_rate_change_pp":      round(recent["error_rate_pct"] - baseline["error_rate_pct"], 2),
            "session_length_change_pct": round(
                100 * ((recent["avg_events_per_session"] - baseline["avg_events_per_session"])
                       / (baseline["avg_events_per_session"] or 1)),
                1,
            ),
        },
    }


# ── Time-windowed behavior change ──────────────────────────────────────────
#
# Unlike the session-count baseline above, this endpoint measures behavior
# change between a calendar-time window (24h / 7d / 30d / custom) and the
# entire history of events before that window (the baseline epoch).
#
# Calculation only starts once there are >= 50 events before the window,
# ensuring the baseline epoch is statistically meaningful.
#
# Composite score (0-100 %):
#   40 % * L1(event_distribution_baseline, event_distribution_current)
#   40 % * L1(transition_baseline,         transition_current)
#   20 % * min(|error_rate_delta_pp| / 20, 1.0)   <- capped at ±20pp = 100 %


async def _baseline_for_timewindow(
    pool,
    tenant_id: str,
    agent_id: str,
    from_ts: datetime | None,
    to_ts: datetime,
) -> dict:
    """Compute baseline metrics for events in [from_ts, to_ts)."""
    if from_ts is None:
        ts_filter = "timestamp < $3"
        params_base = (tenant_id, agent_id, to_ts)
    else:
        ts_filter = "timestamp >= $3 AND timestamp < $4"
        params_base = (tenant_id, agent_id, from_ts, to_ts)

    type_rows = await pool.fetch(
        f"""
        SELECT event_type AS key, COUNT(*) AS count
        FROM events
        WHERE tenant_id = $1 AND agent_id = $2 AND {ts_filter}
        GROUP BY event_type
        """,
        *params_base,
    )

    transition_rows = await pool.fetch(
        f"""
        SELECT
            p.event_type || ' -> ' || c.event_type AS key,
            COUNT(*) AS count
        FROM events c
        JOIN events p ON c.parent_event_id = p.event_id AND p.tenant_id = c.tenant_id
        WHERE c.tenant_id = $1 AND c.agent_id = $2 AND c.{ts_filter.replace('timestamp', 'c.timestamp')}
        GROUP BY p.event_type, c.event_type
        ORDER BY count DESC
        LIMIT 20
        """,
        *params_base,
    )

    error_row = await pool.fetchrow(
        f"""
        SELECT
            COUNT(*) FILTER (
                WHERE event_type ILIKE '%error%'
                   OR event_type ILIKE '%fail%'
                   OR data ? 'error'
            )::float * 100.0 / NULLIF(COUNT(*), 0) AS pct
        FROM events
        WHERE tenant_id = $1 AND agent_id = $2 AND {ts_filter}
        """,
        *params_base,
    )

    total_events = sum(r["count"] for r in type_rows)
    return {
        "events":             total_events,
        "event_distribution": _distribution(type_rows, "key"),
        "transitions":        _distribution(transition_rows, "key"),
        "error_rate_pct":     round(error_row["pct"] or 0, 2),
    }


MINIMUM_BASELINE_EVENTS = 50
MINIMUM_CURRENT_EVENTS  = 10


@router.get("/{agent_id}/behavior-change")
async def agent_behavior_change(
    agent_id: str,
    window: str = Query(default="7d", pattern=r"^(24h|7d|30d|custom)$"),
    from_ts: str | None = Query(default=None),
    to_ts:   str | None = Query(default=None),
    tenant:  dict = Depends(get_tenant),
):
    """
    Time-windowed behavior change for an agent.

    Compares the event distribution and transition patterns inside the selected
    time window against all events before that window (the baseline epoch).
    Returns a composite behavior change percentage (0-100) and a breakdown of
    contributing factors.

    Requires >= 50 events before the window to establish a meaningful baseline.
    """
    agent_id  = _validate_agent_id(agent_id)
    await assert_agent_allowed(tenant, agent_id)
    pool      = get_pool()
    tenant_id = tenant["tenant_id"]

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if window == "24h":
        win_start = now - timedelta(hours=24)
        win_end   = now
    elif window == "7d":
        win_start = now - timedelta(days=7)
        win_end   = now
    elif window == "30d":
        win_start = now - timedelta(days=30)
        win_end   = now
    else:
        if not from_ts or not to_ts:
            raise bad_request(detail="from_ts and to_ts are required for custom window")
        try:
            win_start = datetime.fromisoformat(from_ts.replace("Z", "+00:00")).replace(tzinfo=None)
            win_end   = datetime.fromisoformat(to_ts.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            raise bad_request(detail="from_ts / to_ts must be ISO 8601 timestamps")

    baseline_count = await pool.fetchval(
        "SELECT COUNT(*) FROM events WHERE tenant_id = $1 AND agent_id = $2 AND timestamp < $3",
        tenant_id, agent_id, win_start,
    ) or 0

    if baseline_count < MINIMUM_BASELINE_EVENTS:
        total = await pool.fetchval(
            "SELECT COUNT(*) FROM events WHERE tenant_id = $1 AND agent_id = $2",
            tenant_id, agent_id,
        ) or 0
        return {
            "agent":                    agent_id,
            "status":                   "insufficient_data",
            "message":                  (
                f"Need at least {MINIMUM_BASELINE_EVENTS} events before the selected window to "
                f"establish a baseline epoch. You have {baseline_count} pre-window events "
                f"({total} total). Keep logging."
            ),
            "total_events":             total,
            "baseline_events_needed":   MINIMUM_BASELINE_EVENTS,
            "baseline_events_available": baseline_count,
            "window":                   window,
        }

    current_count = await pool.fetchval(
        "SELECT COUNT(*) FROM events WHERE tenant_id = $1 AND agent_id = $2 AND timestamp >= $3 AND timestamp < $4",
        tenant_id, agent_id, win_start, win_end,
    ) or 0

    if current_count < MINIMUM_CURRENT_EVENTS:
        return {
            "agent":               agent_id,
            "status":              "no_current_data",
            "message":             (
                f"Only {current_count} events in the selected {window} window. "
                f"Need at least {MINIMUM_CURRENT_EVENTS} for a meaningful comparison."
            ),
            "current_event_count": current_count,
            "window":              window,
            "window_start":        win_start.isoformat(),
            "window_end":          win_end.isoformat(),
        }

    baseline_w = await _baseline_for_timewindow(pool, tenant_id, agent_id, None,      win_start)
    current_w  = await _baseline_for_timewindow(pool, tenant_id, agent_id, win_start, win_end)

    dist_shift    = _l1_distance(baseline_w["event_distribution"], current_w["event_distribution"])
    trans_shift   = _l1_distance(baseline_w["transitions"],        current_w["transitions"])
    error_delta   = current_w["error_rate_pct"] - baseline_w["error_rate_pct"]
    error_component = min(abs(error_delta) / 20.0, 1.0)

    behavior_score = 0.4 * dist_shift + 0.4 * trans_shift + 0.2 * error_component
    behavior_change_pct = round(behavior_score * 100, 1)

    if behavior_change_pct < 5:
        verdict = "stable"
    elif behavior_change_pct < 15:
        verdict = "minor"
    elif behavior_change_pct < 30:
        verdict = "noticeable"
    else:
        verdict = "significant"

    biggest = (
        _biggest_changes("event_distribution", baseline_w["event_distribution"], current_w["event_distribution"])
        + _biggest_changes("transitions",       baseline_w["transitions"],        current_w["transitions"])
    )
    biggest.sort(key=lambda d: abs(d["delta_pp"]), reverse=True)

    return {
        "agent":               agent_id,
        "status":              "ok",
        "window":              window,
        "window_start":        win_start.isoformat(),
        "window_end":          win_end.isoformat(),
        "behavior_change_pct": behavior_change_pct,
        "verdict":             verdict,
        "components": {
            "distribution_shift_pct": round(dist_shift  * 100, 1),
            "transition_shift_pct":   round(trans_shift * 100, 1),
            "error_rate_delta_pp":    round(error_delta, 2),
        },
        "baseline_window": {
            "to_ts":       win_start.isoformat(),
            "event_count": baseline_w["events"],
        },
        "current_window": {
            "from_ts":     win_start.isoformat(),
            "to_ts":       win_end.isoformat(),
            "event_count": current_w["events"],
        },
        "biggest_changes": biggest[:8],
    }
